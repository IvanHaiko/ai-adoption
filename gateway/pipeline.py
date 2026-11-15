"""Collect the two core gateways independently and publish source-specific coverage."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from .build import build, fetch, validate_range

METRICS = {"openrouter": ("tokens",), "vercel": ("requests", "tokens", "spend")}


def window(end: str | None, days: int, today: dt.date | None = None) -> tuple[str, str]:
    if not 1 <= days <= 30:
        raise ValueError("window-days must be in 1..30")
    today = today or dt.datetime.now(dt.timezone.utc).date()
    last = dt.date.fromisoformat(end) if end else today - dt.timedelta(days=1)
    first = last - dt.timedelta(days=days - 1)
    validate_range(first.isoformat(), last.isoformat(), today)
    return first.isoformat(), last.isoformat()


def summarize(rows: list[dict], start: str, end: str) -> tuple[list, list, list]:
    """Amounts sum across dates; percentages are means over observed daily shares."""
    groups, candidates = defaultdict(list), defaultdict(list)
    expected = validate_range(start, end)
    for row in rows:
        if row.get("source") not in METRICS or not start <= row.get("date", "") <= end:
            continue
        groups[(row["source"], row["metric"], row["unit"])].append(row)
        if not row["is_other"] and not row.get("hf_repo_id"):
            candidates[(row["source"], row.get("source_model_id") or row["source_name"],
                        row["metric"], row["unit"])].append(row)
    summaries, missing = [], []
    for source, metrics in METRICS.items():
        for metric in metrics:
            observed = {r["date"] for key, items in groups.items()
                        if key[:2] == (source, metric) for r in items}
            missing.extend({"source": source, "date": day, "metric": metric}
                           for day in expected if day not in observed)
    for (source, metric, unit), items in sorted(groups.items()):
        dates = {r["date"] for r in items}
        divisor = len(dates) if unit == "percent" else 1
        total = sum(r["value"] for r in items)
        linked = sum(r["value"] for r in items if r.get("hf_repo_id"))
        other = sum(r["value"] for r in items if r["is_other"])
        unknown = sum(r["value"] for r in items
                      if not r["is_other"] and not r.get("hf_repo_id"))
        summaries.append({
            "source": source, "metric": metric, "unit": unit,
            "aggregation": "mean_daily_share" if unit == "percent" else "sum_tokens",
            "first_day": min(dates), "cutoff": max(dates), "observed_days": len(dates),
            "expected_days": len(expected), "named_models": len({
                r.get("source_model_id") or r["source_name"] for r in items if not r["is_other"]}),
            "source_as_of": sorted({r["as_of"] for r in items if r.get("as_of")}),
            "export_fetched_at": sorted({r["fetched_at"] for r in items if r.get("fetched_at")}),
            "total_value": total / divisor, "other_value": other / divisor,
            "other_fraction": other / total if total else None,
            "linked_value": linked / divisor, "linked_fraction": linked / total if total else None,
            "unlinked_named_value": unknown / divisor,
            "ambiguous_rows": sum(r.get("identity_status") == "ambiguous" for r in items),
            "unresolved_rows": sum(r.get("identity_status") == "unresolved" for r in items),
        })
    queue = []
    for (source, identifier, metric, unit), items in candidates.items():
        dates = {r["date"] for r in items}
        value = sum(r["value"] for r in items)
        queue.append({
            "source": source, "source_identifier": identifier,
            "source_names": sorted({r["source_name"] for r in items}),
            "metric": metric, "unit": unit, "observed_days": len(dates),
            "first_day": min(dates), "last_day": max(dates),
            "priority_value": value / len(dates) if unit == "percent" else value,
            "aggregation": "mean_share_on_observed_days" if unit == "percent" else "sum_tokens",
            "identity_states": sorted({r.get("identity_status", "unresolved") for r in items}),
            "evidence_manifests": sorted({r["manifest_path"] for r in items}),
        })
    queue.sort(key=lambda r: (r["source"], r["metric"], -r["priority_value"],
                              r["source_identifier"]))
    return summaries, queue, missing


def markdown(report: dict) -> str:
    lines = ["# Core gateway report", "", f"UTC window: {report['start']}..{report['end']}",
             f"Run status: {'OK' if report['ok'] else 'FAILED'}. "
             f"Build error: {report['build_error'] or 'none'}.",
             f"HF raw cutoff: {report['hf_raw_cutoff'] or 'unavailable'}; "
             f"HF Gold cutoff: {report['hf_gold_cutoff'] or 'unavailable'}.", ""]
    for item in report["collection"]:
        lines.append(f"- {item['source']}: {item['status']}"
                     + (f" ({item['reason']})" if item.get("reason") else ""))
    lines.extend([
             "", "Each source and metric has its own denominator. Percentages are daily means;",
             "token counts are summed. HF attribution does not establish an open-weight license.",
             "", "| Source | Metric | Cutoff | Days | Named models | Other | HF linked |",
             "|---|---|---|---:|---:|---:|---:|"])
    for row in report["sources"]:
        other = f"{row['other_fraction']:.2%}" if row["other_fraction"] is not None else "unknown"
        linked = (f"{row['linked_fraction']:.2%}"
                  if row["linked_fraction"] is not None else "unknown")
        lines.append(f"| {row['source']} | {row['metric']} | {row['cutoff']} | "
                     f"{row['observed_days']}/{row['expected_days']} | {row['named_models']} | "
                     f"{other} | {linked} |")
    lines.extend(["", f"Missing requested day/metric pairs: {len(report['missing_day_metrics'])}.",
                  f"Unlinked curation candidates: {len(report['curation_queue'])}.", "",
                  "Full evidence and ranked candidates are in core_report.json.", "",
                  "Sources: OpenRouter (openrouter.ai/rankings), as of each archived export;",
                  "Vercel AI Gateway leaderboards. Both usage exports: CC BY 4.0.", ""])
    return "\n".join(lines)


def run(root: Path, end: str | None = None, days: int = 7, offline: bool = False) -> dict:
    root = Path(root)
    start, end = window(end, days)
    collection = []
    for source in METRICS:
        if offline:
            collection.append({"source": source, "status": "offline"})
            continue
        if source == "openrouter" and not os.environ.get("OPENROUTER_API_KEY"):
            collection.append({"source": source, "status": "failed",
                               "reason": "OPENROUTER_API_KEY is required"})
            continue
        try:
            path = fetch(root, source, start, end)
            collection.append({"source": source, "status": "ok",
                               "archive": Path(path).relative_to(root).as_posix()})
        except Exception as exc:
            # Network exception details may include request metadata. Keep this log secret-free.
            collection.append({"source": source, "status": "failed",
                               "reason": type(exc).__name__})
    ledger = root / "data/gateway_identity_links.json"
    out = root / "data/analytics"
    out.mkdir(parents=True, exist_ok=True)
    build_error = None
    try:
        quality = build(root, ledger if ledger.exists() else None)
        rows = pq.read_table(out / "gateway_usage.parquet").to_pylist()
    except Exception as exc:
        build_error = type(exc).__name__
        quality, rows = {}, []
    summaries, queue, missing = summarize(rows, start, end)
    hf_path = root / "data/gold/summary.json"
    hf = json.loads(hf_path.read_text(encoding="utf-8")) if hf_path.exists() else None
    raw_dates = []
    for path in (root / "data/raw").glob("*/_manifest.json"):
        try:
            raw_dates.append(dt.date.fromisoformat(path.parent.name).isoformat())
        except ValueError:
            continue
    raw_cutoff = max(raw_dates) if raw_dates else None
    gold_cutoff = hf.get("latest_snapshot_date") if hf else None
    report = {"start": start, "end": end, "offline": offline, "collection": collection,
              "build_error": build_error, "sources": summaries, "curation_queue": queue,
              "missing_day_metrics": missing, "gateway_revisions": quality.get("revisions"),
              "hf_raw_cutoff": raw_cutoff, "hf_gold_cutoff": gold_cutoff,
              "hf_cutoff": gold_cutoff or raw_cutoff,
              "ok": not build_error and not missing
              and all(item["status"] != "failed" for item in collection)}
    (out / "core_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "core_report.md").write_text(markdown(report), encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--end")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(args.root, args.end, args.window_days, args.offline)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("sources", "curation_queue")}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
