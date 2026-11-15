"""Fetch immutable gateway exports and build source-specific usage observations."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import pyarrow as pa
import pyarrow.parquet as pq

from collector.fetch import HttpClient


def validate_range(start, end, today=None):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if first > last or last >= today:
        raise ValueError("range must contain completed UTC days in increasing order")
    return [(first + dt.timedelta(days=i)).isoformat() for i in range((last-first).days+1)]


def fetch(root, source, start, end, client=None):
    validate_range(start, end)
    headers = {}
    if source == "vercel":
        params = {"dataset": "models", "modality": "text", "from": start, "to": end}
        url = "https://vercel.com/api/ai/leaderboard-export?" + urlencode(params)
    elif source == "openrouter":
        token = os.environ.get("OPENROUTER_API_KEY")
        if not token:
            raise ValueError("OPENROUTER_API_KEY is required")
        headers = {"Authorization": "Bearer " + token}
        params = {"start_date": start, "end_date": end, "period": "day", "modality": "text"}
        url = "https://openrouter.ai/api/v1/datasets/rankings-daily?" + urlencode(params)
    else:
        raise ValueError("unknown source")
    response = (client or HttpClient(timeout=30, max_attempts=3)).get(url, headers=headers)
    if not response.ok:
        raise RuntimeError(f"{source}: HTTP {response.status}; attempts={response.attempts}")
    return archive(root, source, start, end, response.body, url, response.status)


def archive(root, source, start, end, body, url, status=200):
    """Reuse consecutive equal responses; retain a new occurrence after reversions."""
    digest = hashlib.sha256(body).hexdigest()
    parent = Path(root) / "data/external" / source / f"{start}_{end}"
    receipts = []
    for existing in parent.iterdir() if parent.exists() else []:
        if not existing.is_dir():
            continue
        if not (existing / "manifest.json").is_file():
            raise ValueError(f"incomplete archive: {existing}; inspect and quarantine before retry")
        meta = json.loads((existing / "manifest.json").read_text(encoding="utf-8"))
        if meta["url"] == url:
            receipts.append((meta["fetched_at"], existing, meta))
    if receipts:
        _, latest, meta = max(receipts, key=lambda item: item[0])
        if meta["sha256"] == digest:
            if (latest / "response.json").read_bytes() != body:
                raise ValueError("archive hash collision or damaged payload")
            return latest
    folder = parent / digest
    occurrence = 1
    while folder.exists():
        occurrence += 1
        folder = parent / f"{digest}_{occurrence:06d}"
    json.loads(body)  # Reject HTML/error bodies before publishing an archive.
    folder.mkdir(parents=True)
    with (folder / "response.json").open("xb") as handle:
        handle.write(body)
    manifest = {
        "source": source, "start_date": start, "end_date": end, "modality": "text",
        "period": "day", "url": url, "http_status": status, "sha256": digest,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "license": "CC-BY-4.0", "scope": "published top models plus Other",
    }
    if source.startswith("openrouter-"):
        manifest.update(modality=None, period=None, license=None,
                        scope="single model endpoints" if source.endswith("endpoints")
                        else "provider catalogue")
    with (folder / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return folder


def fetch_catalog(root, source, models=None, client=None):
    """Bounded positive-presence evidence; never infer delisting or health."""
    models = models or []
    if source == "openrouter-endpoints":
        if not 1 <= len(models) <= 50 or len(models) != len(set(models)):
            raise ValueError("provide 1–50 distinct author/slug models")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.:~-]+", m) for m in models):
            raise ValueError("invalid model author/slug")
        urls = [(m, f"https://openrouter.ai/api/v1/models/{m}/endpoints") for m in models]
    elif source == "openrouter-providers":
        urls = [("providers", "https://openrouter.ai/api/v1/providers")]
    else:
        raise ValueError("unknown catalogue")
    client = client or HttpClient(timeout=30, max_attempts=3)
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    paths = []
    for model, url in urls:
        response = client.get(url)
        if not response.ok:
            raise RuntimeError(f"{source} {model}: HTTP {response.status}")
        paths.append(str(archive(root, source, day, day, response.body, url)))
    return paths


def normalize(body, manifest):
    payload = json.loads(body)
    source = manifest["source"]
    raw = payload.get("rows") if source == "vercel" else payload.get("data")
    if not isinstance(raw, list):
        raise ValueError(f"{source}: expected rows/data array")
    expected = set(validate_range(manifest["start_date"], manifest["end_date"]))
    rows, keys, totals = [], set(), defaultdict(float)
    for item in raw:
        day = item["date"]
        if day not in expected:
            raise ValueError("response contains date outside requested range")
        if source == "vercel":
            if item.get("modality") != "text" or item.get("group") != "model":
                raise ValueError("unexpected Vercel scope")
            name = item["name"]
            metric, unit = item["metric"], "percent"
            if metric not in {"requests", "tokens", "spend"}:
                raise ValueError("unexpected text metric")
            value, source_id = item["share_percent"], None
        else:
            name = item.get("model_name") or item["model_permaslug"]
            source_id = item["model_permaslug"]
            metric, unit, value = "tokens", "tokens", item["total_tokens"]
            if isinstance(value, str) and value.isascii() and value.isdigit():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("OpenRouter tokens must be a nonnegative integer")
        other = name.lower() == "other" or source_id == "other"
        key = (day, source_id or name, metric)
        if key in keys:
            raise ValueError(f"duplicate observation: {key}")
        keys.add(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("usage value must be numeric")
        if not math.isfinite(value) or value < 0 or (unit == "percent" and value > 100):
            raise ValueError("invalid usage value")
        totals[(day, metric)] += value
        rows.append({
            "source": source, "date": day, "source_name": name,
            "source_model_id": None if other else source_id, "is_other": other,
            "metric": metric, "value": float(value), "unit": unit,
            "modality": "text", "period": "day", "hf_repo_id": None,
            "identity_status": "aggregate" if other else "unresolved",
            "as_of": payload.get("meta", {}).get("as_of"),
            "fetched_at": manifest["fetched_at"], "revision_sha256": manifest["sha256"],
        })
    if source == "vercel":
        for key, total in totals.items():
            if abs(total-100) > 0.05:
                raise ValueError(f"Vercel shares do not sum to 100: {key}: {total}")
    for day, metric in totals:
        if not any(r["date"] == day and r["metric"] == metric and r["is_other"] for r in rows):
            raise ValueError(f"incomplete denominator: missing Other for {day} {metric}")
    missing = [{"date": day, "metric": metric} for day in sorted(expected)
               for metric in (("requests", "tokens", "spend") if source == "vercel"
                              else ("tokens",)) if (day, metric) not in totals]
    return rows, missing


def build(root, ledger=None):
    root = Path(root)
    selected, missing, revisions, batches = {}, [], 0, {}
    for path in sorted((root / "data/external").glob("*/*/*/manifest.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        body = (path.parent / "response.json").read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"damaged archive: {path}")
        if meta["source"] not in {"vercel", "openrouter"}:
            continue
        rows, _ = normalize(body, meta)
        revisions += 1
        # Select a whole source/day/metric revision. A model removed by a later
        # top-list revision must not survive from an earlier export.
        for day in validate_range(meta["start_date"], meta["end_date"]):
            metrics = ("requests", "tokens", "spend") if meta["source"] == "vercel" else ("tokens",)
            for metric in metrics:
                key = (meta["source"], day, metric)
                previous = batches.get(key)
                if previous is None or meta["fetched_at"] > previous[0]:
                    batches[key] = (meta["fetched_at"], [
                        {**r, "manifest_path": path.relative_to(root).as_posix()}
                        for r in rows if r["date"] == day and r["metric"] == metric], meta)
    for (source, day, metric), (_, batch_rows, meta) in sorted(batches.items()):
        if not batch_rows:
            missing.append({"source": source, "date": day, "metric": metric,
                            "revision_sha256": meta["sha256"]})
        for row in batch_rows:
            key = (row["source"], row["date"], row["source_model_id"] or row["source_name"],
                   row["metric"])
            previous = selected.get(key)
            if previous is None or row["fetched_at"] > previous["fetched_at"]:
                selected[key] = row
    links = json.loads(Path(ledger).read_text(encoding="utf-8")) if ledger else []
    rows = sorted(selected.values(), key=lambda r: (r["source"], r["date"], r["metric"],
                                                   r["source_name"]))
    catalogues = {}
    for row in rows:
        evidence, source_targets, source_ambiguous = [], set(), False
        if row["source"] == "openrouter" and not row["is_other"]:
            day = row["date"]
            if day not in catalogues:
                catalogues[day] = _catalogue_links(root, day)
            claims = catalogues[day].get(row["source_model_id"], [])
            declared = {claim["hf_repo_id"] for claim in claims}
            valid = {repo for repo in declared if repo and re.fullmatch(r"[^/\s]+/[^/\s]+", repo)}
            source_targets = valid
            source_ambiguous = bool(valid) and (len(declared) != 1 or len(valid) != 1)
            evidence.extend(claims)
        matches = [link for link in links if not row["is_other"]
                   and link["source"] == row["source"]
                   and (link.get("source_model_id") == row["source_model_id"]
                        if row["source_model_id"] else
                        link.get("source_name") == row["source_name"])
                   and link["valid_from"] <= row["date"] <= link["valid_until"]
                   and link.get("evidence_url") and link.get("reviewer")]
        evidence.extend({"method": "curated", "hf_repo_id": link["hf_repo_id"],
                         "evidence_url": link["evidence_url"], "reviewer": link["reviewer"],
                         "valid_from": link["valid_from"], "valid_until": link["valid_until"]}
                        for link in matches)
        row["identity_evidence"] = json.dumps(evidence, sort_keys=True)
        targets = source_targets | {link["hf_repo_id"] for link in matches}
        if len(targets) == 1 and not source_ambiguous:
            row["hf_repo_id"] = targets.pop()
            row["identity_status"] = "source_explicit" if source_targets else "curated"
        elif len(targets) > 1 or source_ambiguous:
            row["identity_status"] = "ambiguous"
    out = root / "data/analytics"
    out.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows) if rows else pa.table({"source": pa.array([], pa.string())})
    pq.write_table(table, out / "gateway_usage.parquet")
    groups = defaultdict(list)
    for row in rows:
        groups[(row["source"], row["date"], row["metric"], row["unit"])].append(row)
    coverage = []
    for (source, day, metric, unit), items in sorted(groups.items()):
        total = sum(r["value"] for r in items)
        linked = sum(r["value"] for r in items if r["hf_repo_id"])
        coverage.append({"source": source, "date": day, "metric": metric, "unit": unit,
                         "total_observed_value": total, "attributed_value": linked,
                         "attributed_fraction": linked / total if total else None,
                         "linked_rows": sum(bool(r["hf_repo_id"]) for r in items),
                         "other_rows": sum(r["is_other"] for r in items),
                         "other_value": sum(r["value"] for r in items if r["is_other"]),
                         "unresolved_rows": sum(
                             r["identity_status"] == "unresolved" for r in items),
                         "ambiguous_rows": sum(r["identity_status"] == "ambiguous" for r in items),
                         "rows": len(items)})
    report = {"revisions": revisions, "rows": len(rows), "missing_day_metrics": missing,
              "coverage": coverage}
    (out / "gateway_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _catalogue_links(root, day):
    """Source declarations on the same UTC date, never carried backward or forward."""
    path = root / "data/raw" / day / "openrouter_models.json.gz"
    if not path.exists():
        return {}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        catalogue = json.load(handle)
    links = defaultdict(list)
    for index, route in enumerate(catalogue["data"]):
        if route.get("canonical_slug"):
            links[route["canonical_slug"]].append({
                "method": "source_explicit", "hf_repo_id": route.get("hugging_face_id") or "",
                "snapshot_path": path.relative_to(root).as_posix(), "sha256_file": digest,
                "row_index": index, "canonical_slug": route["canonical_slug"],
                "observation_precision": "same_calendar_day"})
    return links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "build"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source", choices=("vercel", "openrouter", "openrouter-endpoints",
                                             "openrouter-providers"))
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        if args.source in {"openrouter-endpoints", "openrouter-providers"}:
            print(json.dumps(fetch_catalog(args.root, args.source, args.model), indent=2))
            return
        if not all((args.source, args.start, args.end)):
            parser.error("fetch requires --source, --start, --end")
        print(fetch(args.root, args.source, args.start, args.end))
    else:
        report = build(args.root, args.ledger)
        print(json.dumps({k: v for k, v in report.items() if k != "coverage"}, indent=2))
