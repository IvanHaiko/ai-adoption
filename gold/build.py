"""Full Gold replay. Every eligibility decision depends on Silver's latest day."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HORIZONS = (7, 14, 30, 60, 90)
MILESTONE_TOLERANCE_DAYS = 1
EARLY_AGE_MAX_DAYS = 4
LOGIC_VERSION = "1"

LIFECYCLE_COLUMNS = (
    "hf_repo_id",
    "author",
    "created_at",
    "first_seen_date",
    "first_seen_source",
    "base_model",
    "library_name",
    "initial_downloads_30d",
    "initial_likes",
    "initial_has_arxiv_reference",
    "initial_arxiv_reference_count",
    "first_nonzero_downloads_date",
    "first_top5000_date",
    "first_openrouter_date",
    "first_arxiv_reference_date",
    "first_resolved_arxiv_reference_date",
    "age_at_first_seen_days",
    "days_to_first_nonzero",
    "days_to_top5000",
    "days_to_openrouter",
    "has_arxiv_reference",
    "resolved_arxiv_reference_count",
    "has_openrouter_link",
    "openrouter_route_count",
    "observed_followup_days",
    "primary_cohort",
    "left_censored_top",
    "left_censored_openrouter",
    "latest_snapshot_date",
    "first_seen_week",
    "first_seen_month",
    *(f"eligible_{h}d" for h in HORIZONS),
    *(f"downloads_30d_at_{h}d" for h in HORIZONS),
    *(f"likes_at_{h}d" for h in HORIZONS),
    *(f"milestone_observed_{h}d" for h in HORIZONS),
    *(f"top_entry_by_{h}d" for h in HORIZONS),
    *(f"sustained_top_by_{h}d" for h in HORIZONS),
)
EVENT_COLUMNS = (
    "hf_repo_id",
    "event_type",
    "event_date",
    "source",
    "event_value",
    "evidence_type",
    "observed_vs_source_timestamp",
    "left_censored",
)
COHORT_COLUMNS = (
    "first_seen_week",
    "horizon_days",
    "cohort_size",
    "eligible_count",
    "milestone_observed_count",
    "top_entry_count",
    "sustained_top_count",
    "top_entry_rate",
    "sustained_top_rate",
    "median_downloads_30d_at_horizon",
)


def _read(silver: Path, name: str) -> list[dict]:
    return pq.read_table(silver / f"{name}.parquet").to_pylist()


def _day(value: str) -> dt.date:
    return dt.date.fromisoformat(value[:10])


def _days(start: str, end: str) -> int:
    return (_day(end) - _day(start)).days


def _first(values: list[str]) -> str | None:
    return min(values) if values else None


def _event(
    events: list[dict],
    repo: str,
    kind: str,
    day: str | None,
    source: str,
    evidence: str,
    observed: bool,
    value=None,
    left_censored=False,
) -> None:
    if day is None:
        return
    events.append(
        {
            "hf_repo_id": repo,
            "event_type": kind,
            "event_date": day,
            "source": source,
            "event_value": str(value) if value is not None else None,
            "evidence_type": evidence,
            "observed_vs_source_timestamp": "collector_observation"
            if observed
            else "source_timestamp",
            "left_censored": left_censored,
        }
    )


def _write(path: Path, name: str, rows: list[dict], columns: tuple[str, ...]) -> None:
    bools = {
        "has_arxiv_reference",
        "initial_has_arxiv_reference",
        "has_openrouter_link",
        "primary_cohort",
        "left_censored_top",
        "left_censored_openrouter",
        "left_censored",
    }
    bools.update(f"eligible_{h}d" for h in HORIZONS)
    bools.update(f"milestone_observed_{h}d" for h in HORIZONS)
    bools.update(f"top_entry_by_{h}d" for h in HORIZONS)
    bools.update(f"sustained_top_by_{h}d" for h in HORIZONS)
    integers = {
        "initial_downloads_30d",
        "initial_likes",
        "initial_arxiv_reference_count",
        "age_at_first_seen_days",
        "days_to_first_nonzero",
        "days_to_top5000",
        "days_to_openrouter",
        "resolved_arxiv_reference_count",
        "openrouter_route_count",
        "observed_followup_days",
        "horizon_days",
        "cohort_size",
        "eligible_count",
        "milestone_observed_count",
        "top_entry_count",
        "sustained_top_count",
    }
    integers.update(f"downloads_30d_at_{h}d" for h in HORIZONS)
    integers.update(f"likes_at_{h}d" for h in HORIZONS)
    floats = {"top_entry_rate", "sustained_top_rate", "median_downloads_30d_at_horizon"}
    schema = pa.schema(
        [
            pa.field(
                key,
                pa.bool_()
                if key in bools
                else pa.int64()
                if key in integers
                else pa.float64()
                if key in floats
                else pa.string(),
            )
            for key in columns
        ]
    )
    table = pa.Table.from_pylist(
        [{key: row.get(key) for key in columns} for row in rows], schema=schema
    )
    pq.write_table(table, path / f"{name}.parquet", compression="zstd")


def build(root: Path, output: Path | None = None) -> dict:
    silver = root / "data/silver"
    output = output or root / "data/gold"
    output.mkdir(parents=True, exist_ok=True)
    cohorts = _read(silver, "hf_repo_cohorts")
    repos = _read(silver, "hf_repositories")
    observations = _read(silver, "hf_observations")
    platform = _read(silver, "model_platform_links")
    paper_links = _read(silver, "hf_arxiv_links")
    papers = _read(silver, "arxiv_papers")
    if not repos or not cohorts:
        raise ValueError("Gold requires nonempty HF Silver tables")
    latest = max(row["snapshot_date"] for row in repos)
    first_day = min(row["snapshot_date"] for row in repos)
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in repos:
        by_repo[row["hf_repo_id"]].append(row)
    top_days: dict[str, list[str]] = defaultdict(list)
    for row in observations:
        if row["source_leg"] == "hf_top_models":
            top_days[row["hf_repo_id"]].append(row["snapshot_date"])
    router_days: dict[str, list[str]] = defaultdict(list)
    route_ids: dict[str, set[str]] = defaultdict(set)
    for row in platform:
        router_days[row["target_entity"]].append(row["first_seen_date"])
        route_ids[row["target_entity"]].add(row["source_entity"])
    ref_days: dict[str, list[str]] = defaultdict(list)
    repo_papers: dict[str, set[str]] = defaultdict(set)
    for row in paper_links:
        ref_days[row["hf_repo_id"]].append(row["first_seen_date"])
        repo_papers[row["hf_repo_id"]].add(row["arxiv_id"])
    paper_first = {}
    for row in papers:
        aid = row["arxiv_id"]
        paper_first[aid] = min(paper_first.get(aid, row["first_seen_date"]), row["first_seen_date"])
    lifecycle, events, errors = [], [], []
    for cohort in sorted(cohorts, key=lambda x: x["hf_repo_id"]):
        rid = cohort["hf_repo_id"]
        history = sorted(by_repo.get(rid, []), key=lambda x: x["snapshot_date"])
        if not history:
            errors.append(f"{rid}: cohort has no canonical observation")
            continue
        first_seen = cohort["first_seen_date"]
        initial_arxiv_ids = json.loads(history[0].get("arxiv_ids") or "[]")
        first_nonzero = _first(
            [
                r["snapshot_date"]
                for r in history
                if r["downloads_30d"] is not None and r["downloads_30d"] > 0
            ]
        )
        top = _first(top_days[rid])
        router = _first(router_days[rid])
        reference = _first(ref_days[rid])
        resolved_dates = [
            max(reference, paper_first[aid])
            for aid in repo_papers[rid]
            if aid in paper_first and reference
        ]
        resolved = _first(resolved_dates)
        followup = _days(first_seen, latest)
        primary = (
            cohort["first_seen_source"] == "hf_new_models"
            and cohort["age_at_first_seen_days"] is not None
            and 0 <= cohort["age_at_first_seen_days"] <= EARLY_AGE_MAX_DAYS
        )
        row = {
            "hf_repo_id": rid,
            "author": history[0]["author"],
            "created_at": cohort["created_at"],
            "first_seen_date": first_seen,
            "first_seen_source": cohort["first_seen_source"],
            "base_model": cohort["base_model"],
            "library_name": history[0]["library_name"],
            "initial_downloads_30d": cohort["initial_downloads_30d"],
            "initial_likes": cohort["initial_likes"],
            "initial_has_arxiv_reference": bool(initial_arxiv_ids),
            "initial_arxiv_reference_count": len(initial_arxiv_ids),
            "first_nonzero_downloads_date": first_nonzero,
            "first_top5000_date": top,
            "first_openrouter_date": router,
            "first_arxiv_reference_date": reference,
            "first_resolved_arxiv_reference_date": resolved,
            "age_at_first_seen_days": cohort["age_at_first_seen_days"],
            "days_to_first_nonzero": _days(first_seen, first_nonzero) if first_nonzero else None,
            "days_to_top5000": _days(first_seen, top) if top else None,
            "days_to_openrouter": _days(first_seen, router) if router else None,
            "has_arxiv_reference": bool(repo_papers[rid]),
            "resolved_arxiv_reference_count": len(repo_papers[rid] & paper_first.keys()),
            "has_openrouter_link": bool(route_ids[rid]),
            "openrouter_route_count": len(route_ids[rid]),
            "observed_followup_days": followup,
            "primary_cohort": primary,
            "left_censored_top": bool(top and top == first_seen),
            "left_censored_openrouter": bool(router and router <= first_seen),
            "latest_snapshot_date": latest,
            "first_seen_week": _day(first_seen).strftime("%G-W%V"),
            "first_seen_month": first_seen[:7],
        }
        for horizon in HORIZONS:
            eligible = followup >= horizon + MILESTONE_TOLERANCE_DAYS
            target = _day(first_seen) + dt.timedelta(days=horizon)
            milestone = next(
                (
                    r
                    for r in history
                    if eligible
                    and 0
                    <= _day(r["snapshot_date"]).toordinal() - target.toordinal()
                    <= MILESTONE_TOLERANCE_DAYS
                ),
                None,
            )
            row[f"eligible_{horizon}d"] = eligible
            row[f"milestone_observed_{horizon}d"] = milestone is not None
            row[f"downloads_30d_at_{horizon}d"] = milestone["downloads_30d"] if milestone else None
            row[f"likes_at_{horizon}d"] = milestone["likes"] if milestone else None
            top_entry = bool(eligible and top and top > first_seen and _day(top) <= target)
            row[f"top_entry_by_{horizon}d"] = top_entry if eligible else None
            row[f"sustained_top_by_{horizon}d"] = (
                sum(first_seen < date <= target.isoformat() for date in set(top_days[rid])) >= 3
                if eligible and top_entry
                else False
                if eligible
                else None
            )
        lifecycle.append(row)
        _event(
            events,
            rid,
            "repo_created",
            cohort["created_at"][:10] if cohort["created_at"] else None,
            "hf_createdAt",
            "source_metadata",
            False,
        )
        _event(
            events,
            rid,
            "first_observed",
            first_seen,
            cohort["first_seen_source"],
            "collector_snapshot",
            True,
        )
        if first_nonzero:
            kind = (
                "first_observed_nonzero"
                if first_nonzero == first_seen
                else "first_nonzero_downloads"
            )
            _event(
                events,
                rid,
                kind,
                first_nonzero,
                "hf_downloads_30d",
                "rolling_30_day_metric",
                True,
                left_censored=first_nonzero == first_seen,
            )
        if top:
            kind = "first_observed_top5000" if top == first_seen else "entered_top5000"
            _event(
                events,
                rid,
                kind,
                top,
                "hf_top_models",
                "sample_membership",
                True,
                left_censored=top == first_seen,
            )
        _event(
            events,
            rid,
            "first_observed_openrouter",
            router,
            "openrouter_models",
            "explicit_hugging_face_id",
            True,
            left_censored=bool(router and router <= first_seen),
        )
        _event(
            events,
            rid,
            "arxiv_reference_detected",
            reference,
            "hf_arxiv_tag",
            "metadata_reference",
            True,
        )
        _event(
            events,
            rid,
            "arxiv_reference_resolved",
            resolved,
            "arxiv_metadata",
            "lookup_enrichment",
            True,
            value=len(repo_papers[rid] & paper_first.keys()),
        )
    metrics = []
    weeks = sorted({r["first_seen_week"] for r in lifecycle if r["primary_cohort"]})
    for week in weeks:
        group = [r for r in lifecycle if r["primary_cohort"] and r["first_seen_week"] == week]
        for horizon in HORIZONS:
            eligible = [r for r in group if r[f"eligible_{horizon}d"]]
            observed = [
                r[f"downloads_30d_at_{horizon}d"]
                for r in eligible
                if r[f"downloads_30d_at_{horizon}d"] is not None
            ]
            top_count = sum(bool(r[f"top_entry_by_{horizon}d"]) for r in eligible)
            sustained = sum(bool(r[f"sustained_top_by_{horizon}d"]) for r in eligible)
            metrics.append(
                {
                    "first_seen_week": week,
                    "horizon_days": horizon,
                    "cohort_size": len(group),
                    "eligible_count": len(eligible),
                    "milestone_observed_count": len(observed),
                    "top_entry_count": top_count,
                    "sustained_top_count": sustained,
                    "top_entry_rate": top_count / len(eligible) if eligible else None,
                    "sustained_top_rate": sustained / len(eligible) if eligible else None,
                    "median_downloads_30d_at_horizon": statistics.median(observed)
                    if observed
                    else None,
                }
            )
    ids = [row["hf_repo_id"] for row in lifecycle]
    if len(ids) != len(set(ids)):
        errors.append("lifecycle HF repo IDs are not unique")
    event_keys = [(r["hf_repo_id"], r["event_type"]) for r in events]
    if len(event_keys) != len(set(event_keys)):
        errors.append("duplicate event type for one HF repo")
    for row in lifecycle:
        rid = row["hf_repo_id"]
        if row["age_at_first_seen_days"] is not None and row["age_at_first_seen_days"] < 0:
            errors.append(f"{rid}: first seen before source created_at")
        for field in ("days_to_first_nonzero", "days_to_top5000", "days_to_openrouter"):
            if row[field] is not None and row[field] < 0:
                errors.append(f"{rid}: negative {field}")
        if row["openrouter_route_count"] != len(route_ids[rid]):
            errors.append(f"{rid}: inconsistent OpenRouter count")
        for h in HORIZONS:
            if row[f"eligible_{h}d"] != (row["observed_followup_days"] >= h + 1):
                errors.append(f"{rid}: inconsistent {h}d eligibility")
            if not row[f"eligible_{h}d"] and row[f"downloads_30d_at_{h}d"] is not None:
                errors.append(f"{rid}: immature {h}d milestone populated")
    _write(output, "model_lifecycle", lifecycle, LIFECYCLE_COLUMNS)
    _write(
        output,
        "lifecycle_events",
        sorted(events, key=lambda r: (r["hf_repo_id"], r["event_type"])),
        EVENT_COLUMNS,
    )
    _write(output, "cohort_metrics", metrics, COHORT_COLUMNS)
    summary = {
        "logic_version": LOGIC_VERSION,
        "latest_snapshot_date": latest,
        "first_snapshot_date": first_day,
        "maximum_followup_days": _days(first_day, latest),
        "lifecycle_rows": len(lifecycle),
        "event_rows": len(events),
        "cohort_metric_rows": len(metrics),
        "primary_cohort_rows": sum(r["primary_cohort"] for r in lifecycle),
        "eligible_primary": {
            str(h): sum(r["primary_cohort"] and r[f"eligible_{h}d"] for r in lifecycle)
            for h in HORIZONS
        },
        "event_counts": dict(Counter(r["event_type"] for r in events)),
        "dq_error_count": len(errors),
        "dq_errors": errors[:100],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gold")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    summary = build(args.root, args.output)
    print(json.dumps(summary, indent=2))
    return 1 if summary["dq_error_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
