"""The new-arrivals leg, and its independence from the ranking leg."""
from __future__ import annotations

import json

from collector import manifest as mf
from collector import paths
from collector.run import collect
from collector.sources import hf_new_models, hf_top_models
from collector.storage import read_gzip
from tests.conftest import HF_LIST, FakeHttp

DATE = "2026-08-29"


def test_the_two_list_legs_walk_different_orderings(tmp_path, http, clock):
    collect(tmp_path, snapshot_date=DATE, http=http, now=clock)

    list_calls = [c for c in http.calls if c.startswith(HF_LIST)]
    assert sum("sort=downloads" in c for c in list_calls) == 3
    assert sum("sort=createdAt" in c for c in list_calls) == 3

    body = read_gzip(paths.leg_path(tmp_path, DATE, "hf_new_models"))
    first = json.loads(body.splitlines()[0])
    assert [r["id"] for r in first["body"]] == ["vendor/createdAt-1-0", "vendor/createdAt-1-1"]


def test_the_legs_are_stored_separately_and_do_not_overwrite_each_other(tmp_path, http, clock):
    manifest = collect(tmp_path, snapshot_date=DATE, http=http, now=clock)

    top = paths.leg_path(tmp_path, DATE, "hf_top_models")
    new = paths.leg_path(tmp_path, DATE, "hf_new_models")
    assert top.exists() and new.exists() and top != new
    assert read_gzip(top) != read_gzip(new)
    assert manifest["legs"]["hf_new_models"]["sort"] == "createdAt"
    assert manifest["legs"]["hf_top_models"]["sort"] == "downloads"


def test_one_leg_failing_does_not_discard_the_other(tmp_path, clock):
    """Independent legs: a ranking outage must not cost us the day's arrivals."""
    flaky = FakeHttp(fail_top_page=2)
    manifest = collect(tmp_path, snapshot_date=DATE, http=flaky, now=clock)

    assert manifest["legs"]["hf_top_models"]["status"] == mf.PARTIAL
    assert manifest["legs"]["hf_new_models"]["status"] == mf.PARTIAL
    assert manifest["status"] == mf.PARTIAL


def test_targets_are_sized_from_measurement_not_round_numbers():
    """Guards the reasoning in the module docstrings against a silent edit.

    TOP_N comes from the download distribution, TARGET_ROWS from the arrival
    rate of ~460-500 repositories a day. Changing either should be a decision,
    not a typo.
    """
    assert hf_top_models.TOP_N == 5000
    assert hf_top_models.SORT == "downloads"
    assert hf_new_models.TARGET_ROWS == 2000
    assert hf_new_models.SORT == "createdAt"
