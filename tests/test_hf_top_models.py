"""The ranking leg: cursor pagination, and why it is all-or-nothing."""
from __future__ import annotations

import json

from collector import manifest as mf
from collector import paths
from collector.run import collect
from collector.storage import read_gzip, read_json, write_json
from tests.conftest import HF_LIST, FakeHttp

DATE = "2026-08-29"


def lines(root, day=DATE):
    return read_gzip(paths.leg_path(root, day, "hf_top_models")).splitlines()


def test_every_page_is_followed_and_stored_one_line_each(tmp_path, http, clock):
    manifest = collect(tmp_path, snapshot_date=DATE, http=http, now=clock)
    leg = manifest["legs"]["hf_top_models"]

    assert leg["status"] == mf.OK
    assert leg["pages"] == 3
    assert leg["rows"] == 6
    assert len(lines(tmp_path)) == 3

    first = json.loads(lines(tmp_path)[0])
    assert first["page"] == 1
    assert [r["id"] for r in first["body"]] == ["vendor/top-1-0", "vendor/top-1-1"]


def test_running_out_of_repositories_is_a_fact_not_a_failure(tmp_path, clock):
    """The category ending before TOP_N says something about the category."""
    manifest = collect(tmp_path, snapshot_date=DATE, http=FakeHttp(), now=clock)
    leg = manifest["legs"]["hf_top_models"]

    assert leg["status"] == mf.OK
    assert leg["reached_target"] is False
    assert leg["rows"] < leg["target_rows"]


def test_a_failed_page_discards_the_whole_leg_rather_than_storing_half(tmp_path, clock):
    """A ranking reorders continuously, so pages from two different runs would
    duplicate and drop repositories while looking like a complete snapshot."""
    flaky = FakeHttp(fail_top_page=2)
    first = collect(tmp_path, snapshot_date=DATE, http=flaky, now=clock)

    assert first["legs"]["hf_top_models"]["status"] == mf.PARTIAL
    assert first["legs"]["hf_top_models"]["failed_on_page"] == 2
    assert not paths.leg_path(tmp_path, DATE, "hf_top_models").exists()
    assert first["status"] == mf.PARTIAL

    healthy = FakeHttp()
    second = collect(tmp_path, snapshot_date=DATE, http=healthy, now=clock)

    assert second["status"] == mf.COMPLETE
    # Restarted from page 1, not resumed at page 2.
    ranking_calls = [c for c in healthy.calls if c.startswith(HF_LIST)]
    assert "cursor" not in ranking_calls[0]
    assert len(ranking_calls) == 3
    assert len(lines(tmp_path)) == 3


def test_a_day_finished_before_the_leg_existed_is_not_reopened(tmp_path, clock):
    """Backfilling would stamp today's ranking with an old date.

    The day is closed and cannot contain this leg; saying so is the honest
    answer, and it keeps the day `complete` so the audit stays quiet.
    """
    collect(tmp_path, snapshot_date=DATE, http=FakeHttp(), now=clock)

    # Rewrite the manifest as an older collector would have left it.
    path = paths.manifest_path(tmp_path, DATE)
    manifest = read_json(path)
    del manifest["legs"]["hf_top_models"]
    write_json(path, manifest)
    paths.leg_path(tmp_path, DATE, "hf_top_models").unlink()

    http = FakeHttp()
    reopened = collect(tmp_path, snapshot_date=DATE, http=http, now=clock)

    assert reopened["legs"]["hf_top_models"]["status"] == mf.NOT_APPLICABLE
    assert reopened["status"] == mf.COMPLETE
    assert http.call_count == 0, "a closed day must not be refetched"
