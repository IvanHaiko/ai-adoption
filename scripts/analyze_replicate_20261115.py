"""Offline exact-link assessment of the two bounded Replicate spikes."""
from __future__ import annotations

import gzip
import hashlib
import json
import statistics
from pathlib import Path
from urllib.parse import unquote, urlparse

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]


def models(folder):
    found = {}
    for path in sorted(folder.glob("*.jsonl.gz")):
        with gzip.open(path, "rb") as stream:
            for line in stream:
                for row in json.loads(line)["body"]["results"]:
                    found.setdefault(f"{row['owner']}/{row['name']}", row)
    return found


def hf_link(url):
    parsed = urlparse(url or "")
    parts = unquote(parsed.path).strip("/").split("/")
    if (parsed.hostname in {"huggingface.co", "www.huggingface.co"}
            and len(parts) >= 2
            and parts[0] not in {"datasets", "spaces", "collections", "papers"}):
        return "/".join(parts[:2])
    return None


def assess(rows, hf_ids, paper_ids):
    exact = []
    for key, row in sorted(rows.items()):
        repo = hf_link(row.get("weights_url"))
        if repo:
            exact.append({"replicate_id": key, "hf_repo_id": repo,
                          "in_project_silver": repo in hf_ids,
                          "method": "explicit_weights_url", "url": row["weights_url"]})
    counts = [row["run_count"] for row in rows.values()
              if isinstance(row.get("run_count"), int)]
    paper_links = []
    for key, row in rows.items():
        parsed = urlparse(row.get("paper_url") or "")
        if parsed.hostname in {"arxiv.org", "www.arxiv.org"}:
            aid = parsed.path.removeprefix("/abs/").removeprefix("/pdf/")
            aid = aid.removesuffix(".pdf").split("v")[0]
            paper_links.append({"replicate_id": key, "arxiv_id": aid,
                                "in_project_silver": aid in paper_ids})
    return {"unique_models": len(rows), "run_count_populated": len(counts),
            "run_count_min": min(counts) if counts else None,
            "run_count_median": statistics.median(counts) if counts else None,
            "run_count_max": max(counts) if counts else None,
            "weights_url_populated": sum(bool(r.get("weights_url")) for r in rows.values()),
            "github_url_populated": sum(bool(r.get("github_url")) for r in rows.values()),
            "paper_url_populated": sum(bool(r.get("paper_url")) for r in rows.values()),
            "exact_hf_links": exact, "arxiv_links": paper_links}


def main():
    current = ROOT / "data/spikes/replicate/2026-11-15"
    old = ROOT / "data/spikes/replicate/2026-10-01"
    hf = set(pq.read_table(ROOT / "data/silver/hf_repo_cohorts.parquet",
                           columns=["hf_repo_id"]).column(0).to_pylist())
    paper_table = pq.read_table(ROOT / "data/silver/arxiv_papers.parquet")
    paper_ids = set(paper_table.column("arxiv_id").to_pylist())
    now, before = models(current), models(old)
    with gzip.open(current / "language_models.json.gz", "rb") as stream:
        collection = json.load(stream)["models"]
    collection = {f"{r['owner']}/{r['name']}": r for r in collection}
    comparisons = []
    for key in sorted(now.keys() & before.keys()):
        a, b = before[key].get("run_count"), now[key].get("run_count")
        if isinstance(a, int) and isinstance(b, int):
            comparisons.append({"replicate_id": key, "before": a, "after": b,
                                "change_between_observations": b-a})
    integrity = []
    for manifest_name in ("_manifest.json", "_collection_manifest.json"):
        manifest = json.loads((current / manifest_name).read_text(encoding="utf-8"))
        entries = manifest.get("strata", {}).values() if "strata" in manifest else [manifest]
        for meta in entries:
            body = gzip.decompress((current / meta["file"]).read_bytes())
            assert hashlib.sha256(body).hexdigest() == meta["sha256_raw"]
            integrity.append(meta["file"])
    report = {"observed_date": "2026-11-15", "comparison_date": "2026-10-01",
              "sample": assess(now, hf, paper_ids),
              "language_models_collection": assess(collection, hf, paper_ids),
              "common_sample_models": len(comparisons),
              "counter_decreases": sum(r["change_between_observations"] < 0
                                       for r in comparisons),
              "counter_comparisons": comparisons, "verified_payloads": integrity,
              "scope": "Biased bounded sample; catalogue total and daily usage unmeasured"}
    (current / "assessment.json").write_text(json.dumps(report, indent=2) + "\n",
                                             encoding="utf-8")
    for label in ("sample", "language_models_collection"):
        data = report[label]
        print(label, {k: v for k, v in data.items() if k not in
                      {"exact_hf_links", "arxiv_links"}})
        print("HF links", len(data["exact_hf_links"]), "project overlap",
              sum(x["in_project_silver"] for x in data["exact_hf_links"]))
        print("arxiv links", len(data["arxiv_links"]), "project overlap",
              sum(x["in_project_silver"] for x in data["arxiv_links"]))
    print("common models", len(comparisons), "decreases", report["counter_decreases"])


if __name__ == "__main__":
    main()
