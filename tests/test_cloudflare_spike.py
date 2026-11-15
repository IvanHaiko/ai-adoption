import pytest

from scripts.cloudflare_spike import normalize_timeseries, queries


def response():
    return {"success": True, "result": {
        "meta": {"normalization": "PERCENTAGE", "aggInterval": "ONE_DAY"},
        "serie_0": {"timestamps": ["2026-11-13T00:00:00Z", "2026-11-14T00:00:00Z"],
                    "model": ["75", "80"], "other": ["25", "20"]}}}


def test_inclusive_end_does_not_leak_boundary_bucket():
    rows, excluded = normalize_timeseries(response(), "2026-11-13", "2026-11-13")
    assert excluded == 1
    assert len(rows) == 2
    assert {row["date"] for row in rows} == {"2026-11-13"}


def test_missing_day_and_incomplete_denominator_fail():
    with pytest.raises(ValueError, match="Missing"):
        normalize_timeseries(response(), "2026-11-12", "2026-11-14")
    broken = response()
    broken["result"]["serie_0"]["other"][0] = "0"
    with pytest.raises(ValueError, match="denominator"):
        normalize_timeseries(broken, "2026-11-13", "2026-11-14")


def test_only_model_dimension_gets_documented_top_limit():
    urls = dict(queries("2026-10-16", "2026-11-14"))
    assert "limitPerGroup=100" in urls["timeseries_groups_model"]
    assert "limitPerGroup" not in urls["timeseries_groups_task"]
