"""The README charts must count the cohort they say they count."""
from __future__ import annotations

from scripts.make_charts import entry_curve, weekly_rates


def _repo(first_seen, days_to_top, eligible_60=True, primary=True):
    return {
        "primary_cohort": primary,
        "eligible_60d": eligible_60,
        "first_seen_date": first_seen,
        "days_to_top5000": days_to_top,
    }


def test_the_curve_uses_one_fixed_cohort():
    """A repo without 60 days of follow-up must not enter the denominator at any day,
    or later and smaller cohorts bend the curve."""
    rows = [
        _repo("2026-09-01", 2),
        _repo("2026-09-02", 20),
        _repo("2026-09-03", None),
        _repo("2026-09-04", None),
        _repo("2026-11-01", 1, eligible_60=False),  # too young: excluded everywhere
        _repo("2026-09-05", 0, primary=False),  # not in the primary cohort
    ]
    curve = entry_curve(rows, 60)

    assert curve["cohort_size"] == 4
    assert (curve["first_seen_from"], curve["first_seen_until"]) == ("2026-09-01", "2026-09-04")
    assert curve["share_by_day"][1] == 0
    assert curve["share_by_day"][2] == 0.25
    assert curve["share_by_day"][60] == 0.5
    assert curve["share_by_day"] == sorted(curve["share_by_day"]), "cumulative, never falls"


def test_a_week_still_inside_its_follow_up_is_not_drawn():
    metrics = [
        {"first_seen_week": "2026-W36", "horizon_days": 14, "cohort_size": 100,
         "eligible_count": 100, "top_entry_count": 4},
        {"first_seen_week": "2026-W35", "horizon_days": 14, "cohort_size": 50,
         "eligible_count": 50, "top_entry_count": 1},
        {"first_seen_week": "2026-W44", "horizon_days": 14, "cohort_size": 80,
         "eligible_count": 30, "top_entry_count": 3},
        {"first_seen_week": "2026-W36", "horizon_days": 7, "cohort_size": 100,
         "eligible_count": 100, "top_entry_count": 2},
    ]
    weeks = weekly_rates(metrics, 14)

    assert [w["week"] for w in weeks] == ["2026-W35", "2026-W36"]
    assert [w["rate"] for w in weeks] == [0.02, 0.04]
