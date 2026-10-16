"""Reconcile research artifacts against pinned observations, independently of analysis.py."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def integer(value):
    return int(value) if value != "" else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    source = read_csv(args.input)
    grouped = defaultdict(list)
    indexed = {}
    for row in source:
        key = row["hf_repo_id"], row["snapshot_date"]
        assert key not in indexed, key
        indexed[key] = row
        grouped[row["hf_repo_id"]].append(row)
    for history in grouped.values():
        history.sort(key=lambda r: r["snapshot_date"])
    summary = json.loads((args.results / "summary.json").read_text(encoding="utf-8"))
    assert summary["canonical_rows"] == len(source)
    assert summary["unique_repositories"] == len(grouped)
    assert summary["input_sha256"] == hashlib.sha256(args.input.read_bytes()).hexdigest()
    assert summary["repeated_repositories"] == sum(len(v) >= 2 for v in grouped.values())
    distribution = {str(k): v for k, v in Counter(map(len, grouped.values())).items()}
    assert summary["observation_count_distribution"] == distribution
    features = read_csv(args.results / "repository_features.csv")
    assert len(features) == len(grouped)
    assert {r["hf_repo_id"] for r in features} == set(grouped)
    features_by_id = {r["hf_repo_id"]: r for r in features}
    eligible = 0
    for feature in features:
        history = grouped[feature["hf_repo_id"]]
        days = [date.fromisoformat(r["snapshot_date"]) for r in history]
        span = (days[-1] - days[0]).days
        gaps = [(days[i] - days[i - 1]).days for i in range(1, len(days))]
        assert integer(feature["observation_count"]) == len(history)
        assert integer(feature["span_days"]) == span
        assert math.isclose(float(feature["coverage_fraction"]), len(days) / (span + 1))
        assert integer(feature["max_gap_days"]) == max(gaps, default=0)
        valid_downloads = sum(r["downloads_30d"] != "" for r in history)
        qualifies = (valid_downloads >= 21 and span >= 28
                     and len(days) / (span + 1) >= 0.8 and max(gaps, default=0) <= 3)
        assert (feature["eligible"] == "True") == qualifies
        eligible += qualifies
        for metric, prefix in (("downloads_30d", "download"), ("likes", "likes")):
            values = [r for r in history if r[metric] != ""]
            first = int(values[0][metric]) if values else None
            last = int(values[-1][metric]) if values else None
            change = last - first if values else None
            assert integer(feature[prefix + "_first"]) == first
            assert integer(feature[prefix + "_last"]) == last
            assert integer(feature[prefix + "_absolute_change"]) == change
            assert feature[prefix + "_first_date"] == (values[0]["snapshot_date"] if values else "")
            assert feature[prefix + "_last_date"] == (values[-1]["snapshot_date"] if values else "")
    assert eligible == summary["baseline_eligible"]
    intervals = read_csv(args.results / "interval_changes.csv")
    expected_pairs = {(repo, history[i - 1]["snapshot_date"], history[i]["snapshot_date"])
                      for repo, history in grouped.items() for i in range(1, len(history))}
    actual_pairs = {(r["hf_repo_id"], r["from_date"], r["to_date"]) for r in intervals}
    assert len(intervals) == len(actual_pairs) == len(expected_pairs) == summary["intervals"]
    assert actual_pairs == expected_pairs
    for interval in intervals:
        repo = interval["hf_repo_id"]
        left = indexed[repo, interval["from_date"]]
        right = indexed[repo, interval["to_date"]]
        if left["fetched_at"] and right["fetched_at"]:
            elapsed = (datetime.fromisoformat(right["fetched_at"])
                       - datetime.fromisoformat(left["fetched_at"])).total_seconds() / 86400
        else:
            elapsed = (date.fromisoformat(right["snapshot_date"])
                       - date.fromisoformat(left["snapshot_date"])).days
        assert elapsed > 0 and math.isclose(float(interval["elapsed_days"]), elapsed)
        for metric, name in (("downloads_30d", "download_change"), ("likes", "likes_change")):
            a, b = integer(left[metric]), integer(right[metric])
            delta = b - a if a is not None and b is not None else None
            assert integer(interval[name]) == delta
            if metric == "downloads_30d":
                rate = interval["download_change_per_day"]
                assert (rate == "") if delta is None else math.isclose(float(rate), delta / elapsed)
    cases = read_csv(args.results / "cases.csv")
    selected = {r["hf_repo_id"] for r in cases}
    assert selected == set(summary["selected_cases"])
    case_rows = read_csv(args.results / "case_observations.csv")
    case_keys = [(r["hf_repo_id"], r["snapshot_date"]) for r in case_rows]
    assert len(case_keys) == len(set(case_keys))
    assert set(case_keys) == {key for key in indexed if key[0] in selected}
    for row in case_rows:
        original = indexed[row["hf_repo_id"], row["snapshot_date"]]
        assert all(row[k] == original[k] for k in original)
    for case in cases:
        feature = features_by_id[case["hf_repo_id"]]
        assert all(v == feature[k] for k, v in case.items() if k in feature)
    compared = {}
    if args.compare:
        for path in sorted(args.results.rglob("*")):
            if path.is_file():
                name = path.relative_to(args.results)
                counterpart = args.compare / name
                same = counterpart.is_file() and path.read_bytes() == counterpart.read_bytes()
                assert same, f"Reproduction mismatch: {name}"
                compared[name.as_posix()] = True
    receipt = {"accepted": True, "source_rows": len(source), "exact_repositories": len(grouped),
               "eligible_histories": eligible, "reconciled_intervals": len(intervals),
               "reconciled_case_observations": len(case_rows), "case_ids": sorted(selected),
               "byte_identical_reproduced_files": compared,
               "scope": "Source-to-table arithmetic and membership; "
                        "no causal/predictive validation"}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
