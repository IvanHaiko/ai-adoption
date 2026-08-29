"""The audit has to catch what the collector cannot see about itself."""
from __future__ import annotations

import datetime as dt

import pytest

from collector import paths
from collector.audit import ERROR, WARN, audit
from collector.run import collect
from collector.storage import gzip_bytes, read_json, write_json
from tests.conftest import FakeHttp

TODAY = dt.date(2026, 8, 29)


def make_day(root, day, http=None, clock=None):
    http = http or FakeHttp()
    collect(root, snapshot_date=day, http=http, now=clock or (lambda: f"{day}T00:00:00+00:00"))


def levels(findings, check):
    return [f.level for f in findings if f.check == check]


def test_a_healthy_run_of_days_reports_nothing(tmp_path):
    for day in ("2026-08-27", "2026-08-28", "2026-08-29"):
        make_day(tmp_path, day)
    assert audit(tmp_path, today=TODAY) == []


def test_an_empty_tree_is_an_error_not_a_pass(tmp_path):
    findings = audit(tmp_path, today=TODAY)
    assert levels(findings, "staleness") == [ERROR]


def test_a_hole_in_the_middle_is_found(tmp_path):
    make_day(tmp_path, "2026-08-26")
    make_day(tmp_path, "2026-08-29")
    findings = audit(tmp_path, today=TODAY)

    gaps = [f for f in findings if f.check == "gap"]
    assert [f.day for f in gaps] == ["2026-08-27", "2026-08-28"]


def test_lag_is_reported_once_and_not_also_as_gaps(tmp_path):
    """The distance from the last snapshot to today is staleness, not holes."""
    make_day(tmp_path, "2026-08-22")
    make_day(tmp_path, "2026-08-23")
    findings = audit(tmp_path, today=TODAY)

    assert levels(findings, "staleness") == [ERROR]
    assert levels(findings, "gap") == []


def test_yesterday_being_the_latest_day_is_healthy(tmp_path):
    make_day(tmp_path, "2026-08-28")
    assert audit(tmp_path, today=TODAY) == []


def test_corrupted_bytes_are_caught_by_the_recorded_hash(tmp_path):
    day = "2026-08-29"
    make_day(tmp_path, day)

    # Replace the payload and repair the byte count, so the size check passes
    # and the SHA-256 is the only thing standing between us and silent rot.
    target = paths.leg_path(tmp_path, day, "openrouter_models")
    forged = gzip_bytes(b'{"data": []}')
    target.write_bytes(forged)
    manifest_path = paths.manifest_path(tmp_path, day)
    manifest = read_json(manifest_path)
    manifest["legs"]["openrouter_models"]["bytes_stored"] = len(forged)
    write_json(manifest_path, manifest)

    findings = audit(tmp_path, today=TODAY)
    integrity = [f for f in findings if f.check == "integrity"]
    assert len(integrity) == 1
    assert "does not match manifest" in integrity[0].message


def test_truncation_is_caught_by_the_byte_count(tmp_path):
    day = "2026-08-29"
    make_day(tmp_path, day)
    target = paths.leg_path(tmp_path, day, "hf_models")
    target.write_bytes(target.read_bytes()[:-20])

    findings = audit(tmp_path, today=TODAY)
    assert levels(findings, "integrity") == [ERROR]


def test_a_missing_file_is_not_silently_forgiven(tmp_path):
    day = "2026-08-29"
    make_day(tmp_path, day)
    paths.leg_path(tmp_path, day, "openrouter_rankings").unlink()

    findings = audit(tmp_path, today=TODAY)
    assert levels(findings, "integrity") == [ERROR]


def test_a_thin_day_is_caught_even_though_it_is_complete(tmp_path):
    """The failure the collector is blind to.

    Every response was a legitimate answer, the manifest is internally
    consistent, and the day is `complete` — with most of its rows absent.
    """
    make_day(tmp_path, "2026-08-28")
    make_day(tmp_path, "2026-08-29", http=FakeHttp(hf_status={"Vendor/Beta": 404}))

    manifest = read_json(paths.manifest_path(tmp_path, "2026-08-29"))
    assert manifest["status"] == "complete", "the collector sees nothing wrong"

    findings = audit(tmp_path, today=TODAY)
    coverage = [f for f in findings if f.check == "coverage"]
    assert [f.level for f in coverage] == [ERROR]
    assert coverage[0].day == "2026-08-29"
    assert "1/3" in coverage[0].message


def test_a_shrinking_catalogue_is_reported_separately_from_lost_rows(tmp_path):
    """Fewer repositories asked for is a different fact from fewer answered."""
    make_day(tmp_path, "2026-08-28")
    make_day(tmp_path, "2026-08-29", http=FakeHttp(entries=[("vendor/alpha", "Vendor/Alpha")]))

    findings = audit(tmp_path, today=TODAY)
    assert levels(findings, "catalogue") == [WARN]
    assert levels(findings, "coverage") == [], "coverage went up, not down"


@pytest.mark.parametrize("status", ["partial", "pending"])
def test_a_day_that_never_finished_is_an_error(tmp_path, status):
    day = "2026-08-29"
    make_day(tmp_path, day)
    manifest_path = paths.manifest_path(tmp_path, day)
    manifest = read_json(manifest_path)
    manifest["status"] = status
    write_json(manifest_path, manifest)

    findings = audit(tmp_path, today=TODAY)
    assert levels(findings, "incomplete") == [ERROR]


def test_an_acknowledged_gap_stops_being_an_error(tmp_path):
    """A permanent loss must be admissible, or the audit stays red forever and
    nobody reads it by the time the next gap appears."""
    make_day(tmp_path, "2026-08-27")
    make_day(tmp_path, "2026-08-29")

    assert levels(audit(tmp_path, today=TODAY), "gap") == [ERROR]

    (tmp_path / "known_gaps.txt").write_text(
        "# comment\n2026-08-28  runner outage, both scheduled runs failed\n",
        encoding="utf-8",
    )
    gaps = [f for f in audit(tmp_path, today=TODAY) if f.check == "gap"]
    assert [f.level for f in gaps] == [WARN]
    assert "runner outage" in gaps[0].message


def test_acknowledging_one_gap_does_not_excuse_another(tmp_path):
    make_day(tmp_path, "2026-08-26")
    make_day(tmp_path, "2026-08-29")
    (tmp_path / "known_gaps.txt").write_text("2026-08-27  known\n", encoding="utf-8")

    gaps = {f.day: f.level for f in audit(tmp_path, today=TODAY) if f.check == "gap"}
    assert gaps == {"2026-08-27": WARN, "2026-08-28": ERROR}


def test_a_thin_ranking_day_is_caught(tmp_path):
    """Every page returned 200, the leg is ok, and most rows never arrived."""
    make_day(tmp_path, "2026-08-28")
    make_day(tmp_path, "2026-08-29", http=FakeHttp(top_pages=1))

    manifest = read_json(paths.manifest_path(tmp_path, "2026-08-29"))
    assert manifest["status"] == "complete", "the collector sees nothing wrong"
    assert manifest["legs"]["hf_top_models"]["status"] == "ok"

    findings = audit(tmp_path, today=TODAY)
    ranking = [f for f in findings if f.check == "ranking"]
    assert [f.level for f in ranking] == [ERROR]
    assert "2 rows against 6" in ranking[0].message
