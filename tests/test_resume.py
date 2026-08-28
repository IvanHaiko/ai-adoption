"""Partial days. A transient failure must leave work for the next run; a
permanent one must not."""
from __future__ import annotations

import json

from collector import manifest as mf
from collector import paths
from collector.run import collect
from collector.storage import read_gzip
from tests.conftest import HF_API, RANKINGS_URL, FakeHttp

DATE = "2026-08-29"


def test_transient_failure_leaves_the_day_partial_and_is_retried(tmp_path, clock):
    down = FakeHttp(fail_urls={RANKINGS_URL: 503})
    first = collect(tmp_path, snapshot_date=DATE, http=down, now=clock)
    assert first["status"] == mf.PARTIAL
    assert first["legs"]["openrouter_rankings"]["status"] == mf.PARTIAL

    healthy = FakeHttp()
    second = collect(tmp_path, snapshot_date=DATE, http=healthy, now=clock)
    assert second["status"] == mf.COMPLETE
    # Only the failed leg was retried; the ones that succeeded were not re-asked.
    assert healthy.calls == [RANKINGS_URL]


def test_hf_resume_only_fetches_what_is_missing(tmp_path, clock):
    flaky = FakeHttp(hf_status={"Vendor/Beta": 503})
    first = collect(tmp_path, snapshot_date=DATE, http=flaky, now=clock)
    assert first["status"] == mf.PARTIAL
    assert first["legs"]["hf_models"]["stored"] == 1  # Alpha only

    healthy = FakeHttp()
    second = collect(tmp_path, snapshot_date=DATE, http=healthy, now=clock)
    assert second["status"] == mf.COMPLETE
    assert healthy.calls == [HF_API + "Vendor/Beta"]
    assert second["legs"]["hf_models"]["stored"] == 2


def test_a_gone_repository_is_recorded_as_a_failure_not_a_zero(tmp_path, http, clock):
    manifest = collect(tmp_path, snapshot_date=DATE, http=http, now=clock)
    leg = manifest["legs"]["hf_models"]

    assert leg["status"] == mf.OK, "a permanent failure must not block the day"
    assert leg["requested"] == 3
    assert leg["stored"] == 2
    assert leg["failures"] == [
        {"hf_id": "Vendor/Gone", "http_status": 401,
         "attempted_at": clock(), "error": "HTTP 401"}
    ]

    body = read_gzip(paths.leg_path(tmp_path, DATE, "hf_models"))
    ids = [json.loads(line)["hf_id"] for line in body.splitlines()]
    assert ids == ["Vendor/Alpha", "Vendor/Beta"], "no row invented for the missing repo"
