"""Read immutable Bronze; publish deterministic quality and hosting projections."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from collector.paths import LEG_FILES, repo_root


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _evidence(root, folder, leg):
    manifest = folder / "_manifest.json"
    return {
        "manifest_path": manifest.relative_to(root).as_posix(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "payload_sha256": leg.get("sha256_raw"),
    }


def _envelopes(folder, leg):
    payload = gzip.decompress((folder / leg["file"]).read_bytes())
    if leg.get("sha256_raw") and hashlib.sha256(payload).hexdigest() != leg["sha256_raw"]:
        raise ValueError(f"payload hash mismatch: {folder / leg['file']}")
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def project(root):
    manifests = {p.parent.name: (p.parent, _json(p))
                 for p in sorted((root / "data/raw").glob("*/_manifest.json"))}
    if not manifests:
        raise ValueError("No Bronze manifests")
    days = sorted(manifests)
    start, cutoff = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    source_start = {}
    for day, (_, manifest) in manifests.items():
        for source in manifest.get("legs", {}):
            source_start.setdefault(source, day)
    coverage, cohort, queue, hosting = [], [], [], []
    registries = {}
    active_cohort = None
    for offset in range((cutoff - start).days + 1):
        day = (start + dt.timedelta(days=offset)).isoformat()
        folder, manifest = manifests.get(day, (root / "data/raw" / day, {}))
        for source in sorted(LEG_FILES):
            leg = manifest.get("legs", {}).get(source)
            status = (leg.get("status", "unknown") if leg else
                      "not_started" if day < source_start.get(source, "9999") else "missing")
            coverage.append({
                "snapshot_date": day, "source": source, "status": status,
                "rows": leg.get("rows", leg.get("stored")) if leg else None,
                "pages": leg.get("pages") if leg else None,
                "target_rows": leg.get("target_rows") if leg else None,
                "reached_target": leg.get("reached_target") if leg else None,
                "scope": json.dumps({k: leg[k] for k in ("scope", "filter", "sort")
                                     if k in leg}, sort_keys=True) if leg else None,
                "collection_status": manifest.get("status", "missing"),
                **(_evidence(root, folder, leg) if leg else {
                    "manifest_path": None, "manifest_sha256": None, "payload_sha256": None}),
            })
        legs = manifest.get("legs", {})
        leg = legs.get("hf_cohort", {})
        if leg.get("cohort_id") and leg.get("file"):
            cid = leg["cohort_id"]
            active_cohort = cid
            if cid not in registries:
                registries[cid] = _json(root / "data/cohorts" / f"{cid}.json")
            members = registries[cid]["members"]
            registry_path = root / "data/cohorts" / f"{cid}.json"
            cohort_evidence = {
                **_evidence(root, folder, leg),
                "registry_path": registry_path.relative_to(root).as_posix(),
                "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            }
            successful = {r["hf_id"] for r in _envelopes(folder, leg)}
            failures = {r["hf_id"]: r for r in leg.get("failures", [])}
            deferred = {r["hf_id"]: r for r in leg.get("deferred", [])}
            counters = defaultdict(Counter)
            for member in members:
                if not member.get("active", True):
                    continue
                repo, group = member["hf_repo_id"], member["selection_group"]
                reason = ("observed" if repo in successful else
                          failures.get(repo, {}).get("reason") or
                          deferred.get(repo, {}).get("reason") or "unobserved")
                counters[group][reason] += 1
                failure = failures.get(repo, {})
                if failure.get("reason") == "identity_mismatch":
                    queue.append({"snapshot_date": day, "cohort_id": cid,
                                  "selection_group": group, "requested_id": repo,
                                  "response_id": failure.get("response_id"),
                                  "http_status": failure.get("http_status"),
                                  "decision": "pending_review",
                                  **cohort_evidence})
            for group, counts in sorted(counters.items()):
                total = sum(counts.values())
                cohort.append({"snapshot_date": day, "cohort_id": cid,
                               "source_available": True,
                               "selection_group": group, "eligible_members": total,
                               "observed_members": counts["observed"],
                               "coverage": counts["observed"] / total,
                               "missing_reasons": json.dumps(
                                   {k: v for k, v in counts.items() if k != "observed"},
                                   sort_keys=True), **cohort_evidence})
        elif active_cohort:
            registry_path = root / "data/cohorts" / f"{active_cohort}.json"
            groups = Counter(m["selection_group"] for m in registries[active_cohort]["members"]
                             if m.get("active", True))
            for group, total in sorted(groups.items()):
                cohort.append({
                    "snapshot_date": day, "cohort_id": active_cohort,
                    "source_available": False, "selection_group": group,
                    "eligible_members": total, "observed_members": 0, "coverage": 0.0,
                    "missing_reasons": json.dumps({"source_unavailable": total}),
                    "manifest_path": None, "manifest_sha256": None, "payload_sha256": None,
                    "registry_path": registry_path.relative_to(root).as_posix(),
                    "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
                })
        leg = legs.get("hf_inference_providers", {})
        if leg.get("file"):
            hosting_evidence = _evidence(root, folder, leg)
            repos = {}
            for page_number, envelope in enumerate(_envelopes(folder, leg), 1):
                for row_number, row in enumerate(envelope["body"], 1):
                    entry = repos.setdefault(row["id"], {"live": set(), "all": set(),
                                                         "statuses": Counter(), "rows": []})
                    entry["rows"].append([page_number, row_number])
                    for mapping in row.get("inferenceProviderMapping") or []:
                        entry["all"].add(mapping["provider"])
                        entry["statuses"][mapping.get("status", "unknown")] += 1
                        if mapping.get("status") == "live":
                            entry["live"].add(mapping["provider"])
            for repo, entry in sorted(repos.items()):
                hosting.append({"snapshot_date": day, "hf_repo_id": repo,
                                "source": "hf_inference_providers",
                                "live_provider_count": len(entry["live"]),
                                "declared_provider_count": len(entry["all"]),
                                "live_providers": json.dumps(sorted(entry["live"])),
                                "status_counts": json.dumps(entry["statuses"], sort_keys=True),
                                "mapping_state": "declared" if entry["all"] else "unknown",
                                "source_status": leg.get("status", "unknown"),
                                "row_locators": json.dumps(entry["rows"]),
                                **hosting_evidence})
    return {"source_coverage": coverage, "cohort_coverage": cohort,
            "identity_review_queue": queue, "hosting_day": hosting}


def build(root: Path):
    tables = project(root)
    output = root / "data/analytics"
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        pq.write_table(pa.Table.from_pylist(rows), output / f"{name}.parquet",
                       compression="zstd")
    summary = {"logic_version": "1", "cutoff": max(
        r["snapshot_date"] for r in tables["source_coverage"]),
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "cohort_latest": [r for r in tables["cohort_coverage"] if r["snapshot_date"] == max(
            (r["snapshot_date"] for r in tables["cohort_coverage"]), default=None)],
        "interpretation": "Observed catalogue declarations, not verified inference or absence"}
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root())
    args = parser.parse_args()
    print(json.dumps(build(args.root), indent=2))
