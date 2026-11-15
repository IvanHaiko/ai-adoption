"""Readable cards for exactly linked OpenRouter models outside the newest cohort."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq


def build(root: Path) -> dict:
    lifecycle = pq.read_table(root / "data/gold/model_lifecycle.parquet").to_pylist()
    rows = [r for r in lifecycle if r["has_openrouter_link"]]
    cards = [{k: r[k] for k in (
        "hf_repo_id", "created_at", "first_seen_date", "first_seen_source",
        "primary_cohort", "first_top5000_date", "first_openrouter_date",
        "initial_downloads_30d", "initial_likes", "days_to_openrouter",
    )} for r in sorted(rows, key=lambda r: r["hf_repo_id"])]
    usage_path = root / "data/analytics/gateway_usage.parquet"
    usage = pq.read_table(usage_path).to_pylist() if usage_path.exists() else []
    for card in cards:
        linked = [r for r in usage if r.get("hf_repo_id") == card["hf_repo_id"]]
        card["gateway_usage"] = [{k: r[k] for k in (
            "source", "date", "metric", "value", "unit", "identity_status",
            "manifest_path",
        )} for r in linked]
    summary = json.loads((root / "data/gold/summary.json").read_text(encoding="utf-8"))
    return {"cutoff": summary["latest_snapshot_date"], "cards": cards,
            "interpretation": "Exact HF declarations only; observation dates are not release dates",
            "count": len(cards), "outside_primary": sum(not r["primary_cohort"] for r in cards)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = build(args.root)
    out = args.root / "data/analytics"
    out.mkdir(parents=True, exist_ok=True)
    (out / "flagship_cards.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cards"}))


if __name__ == "__main__":
    main()
