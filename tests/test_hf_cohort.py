"""Offline implementation checks; no live services or historical writes."""

import base64
import json

import pytest

from collector.cohort import initialize, registry_path, select_members
from collector.fetch import Response
from collector.run import Context, collect
from collector.sources import hf_cohort
from collector.storage import write_gzip, write_json

DAY = "2026-10-14"
NOW = DAY + "T10:00:00+00:00"


def candidate(rid, downloads=10, **extra):
    return dict(
        hf_repo_id=rid,
        downloads=downloads,
        likes=1,
        created_at="2026-09-01T00:00:00Z",
        author=rid.split("/")[0],
        historical_observation_days=5,
        **extra,
    )


class Http:
    min_interval = 0.25
    call_count = 0

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url):
        assert self.min_interval >= 0.75
        self.calls.append(url)
        self.call_count += 1
        status, body = self.responses.pop(0)
        return Response(url, status, body)


def registry(ids=("a/x",)):
    return dict(
        cohort_id="fixed",
        selected_at=NOW,
        members=[dict(hf_repo_id=i, active=True, enrolled_at=NOW) for i in ids],
    )


def context(tmp_path, responses=()):
    return Context(tmp_path, DAY, Http(responses), lambda: NOW)


def envelope(body, fetched=NOW, **extra):
    return (
        json.dumps(dict(body=body, fetched_at=fetched, http_status=200, **extra)).encode() + b"\n"
    )


def test_selection_is_order_independent_and_deduplicated():
    rows = [candidate(f"{org}/{i}", i) for org in ("b", "a") for i in range(8)]
    selected = select_members(rows + [rows[0]], 9)
    assert selected == select_members(list(reversed(rows)), 9)
    assert len({m["hf_repo_id"] for m in selected}) == 9
    assert [m["selection_group"] for m in selected] == ["popular"] * 3 + ["recent"] * 3 + [
        "remaining"
    ] * 3
    assert selected[0]["hf_repo_id"] == "a/7"
    assert all(m["historical_observation_days"] == 5 for m in selected)


@pytest.mark.parametrize(
    "field,value",
    [
        ("downloads", True),
        ("likes", -1),
        ("downloads", None),
        ("private", True),
        ("disabled", True),
        ("hf_repo_id", "../bad"),
    ],
)
def test_selection_excludes_invalid_candidates(field, value):
    row = candidate("a/x")
    row[field] = value
    assert select_members([row]) == []


def test_remaining_spreads_organizations_and_fills_shortfalls():
    rows = [candidate(f"a/{i}") for i in range(10)] + [candidate("b/x")]
    for row in rows:
        row["created_at"] = None
    result = select_members(rows, 6)
    first_round = [m for m in result if m["selection_group"] == "remaining"][:2]
    assert {m["hf_repo_id"].split("/")[0] for m in first_round} == {"a", "b"}
    assert len(result) == 6


def test_registry_uses_completed_history_and_is_fixed(tmp_path):
    folder = tmp_path / "data/raw/2026-09-01"
    write_json(
        folder / "_manifest.json",
        {"legs": {"hf_models": {"status": "ok"}, "hf_top_models": {"status": "partial"}}},
    )
    body = dict(id="a/x", downloads=2, likes=1, createdAt="2026-08-01T00:00:00Z")
    write_gzip(folder / "hf_models.jsonl.gz", envelope(body, "2026-09-01T00:00:00Z", hf_id="a/x"))
    result = initialize(tmp_path, "fixed", "2026-09-02T00:00:00Z", selected_at=NOW)
    path = registry_path(tmp_path, "fixed")
    before = path.read_bytes()
    assert result["members"][0]["hf_repo_id"] == "a/x"
    assert result["eligible_candidates"][0]["historical_observation_days"] == 1
    assert initialize(tmp_path, "fixed", "invalid", size=2) == result
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "cohort_id,cutoff", [("../bad", NOW), ("safe", "2026-10-14"), ("safe", "2027-01-01T00:00:00Z")]
)
def test_registry_rejects_unsafe_parameters(tmp_path, cohort_id, cutoff):
    with pytest.raises(ValueError):
        initialize(tmp_path, cohort_id, cutoff, selected_at=NOW)


def test_reuses_discovery_precedence_and_all_sources(tmp_path):
    ctx = context(tmp_path)
    ctx.store("hf_models", envelope(dict(id="a/x", downloads=2, likes=1), hf_id="a/x"))
    ctx.store("hf_top_models", envelope([dict(id="a/x", downloads=99, likes=1)]))
    leg = hf_cohort.collect(ctx, {}, registry())
    row = json.loads(ctx.load("hf_cohort"))
    assert row["body"]["downloads"] == 2
    assert row["discovery_sources"] == ["hf_models", "hf_top_models"]
    assert leg["coverage"]["already_observed_through_discovery"] == 1
    assert ctx.http.calls == []


def test_verbatim_success_and_terminal_failure_survive_rerun(tmp_path):
    raw = b'{ "id":"a/x", "downloads":3, "likes":0 }'
    ctx = context(tmp_path, [(200, raw), (404, b"{}")])
    leg = hf_cohort.collect(ctx, {}, registry(("a/x", "b/x")))
    assert raw in ctx.load("hf_cohort")
    assert leg["status"] == "ok"
    assert leg["coverage"]["coverage"] == 0.5
    assert ctx.http.min_interval == 0.25
    rerun = context(tmp_path)
    assert hf_cohort.collect(rerun, leg, registry(("a/x", "b/x")))["stored"] == 1
    assert rerun.http.calls == []


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_multiline_response_is_lossless_and_survives_partial_rerun(tmp_path, newline):
    import pyarrow.parquet as pq

    from silver.build import build

    raw = newline.join(
        [
            b"{",
            b'  "id": "a/x", "author": "a", "downloads": 1234, "likes": 17,',
            b'  "createdAt": "2026-09-01T00:00:00Z",',
            b'  "lastModified": "2026-10-13T12:00:00Z",',
            b'  "pipeline_tag": "text-generation", "library_name": "transformers",',
            b'  "tags": ["text-generation", "license:apache-2.0"],',
            b'  "config": {"architectures": ["LlamaForCausalLM"]}',
            b"}",
            b"",
        ]
    )
    reg = registry(("a/x", "b/x"))
    ctx = context(tmp_path, [(200, raw), (503, b"{}")])
    leg = hf_cohort.collect(ctx, {}, reg)
    saved = ctx.load("hf_cohort")
    assert leg["status"] == "partial"
    assert leg["stored"] == 1
    assert saved.count(b"\n") == 1
    assert b"\r" not in saved
    row = json.loads(saved)
    assert base64.b64decode(row["body_raw_base64"], validate=True) == raw
    assert row["body"] == json.loads(raw)
    assert row["hf_id"] == "a/x"
    assert row["cohort_id"] == "fixed"
    assert row["fetched_at"] == NOW
    assert row["source_leg"] == "hf_cohort"
    assert row["source_url"] == "https://huggingface.co/api/models/a/x"
    assert row["discovery_sources"] == []
    write_json(
        tmp_path / "data/raw" / DAY / "_manifest.json",
        {"snapshot_date": DAY, "status": "partial", "legs": {"hf_cohort": leg}},
    )
    assert build(tmp_path)["errors_count"] == 0
    observations = pq.read_table(tmp_path / "data/silver/hf_observations.parquet").to_pylist()
    assert len(observations) == 1
    assert observations[0]["downloads_30d"] == 1234
    assert observations[0]["likes"] == 17

    retry = context(tmp_path, [(503, b"{}")])
    resumed = hf_cohort.collect(retry, leg, reg)
    assert retry.http.calls == ["https://huggingface.co/api/models/b/x"]
    assert resumed["stored"] == 1
    assert resumed["status"] == "partial"
    assert retry.load("hf_cohort") == saved


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (200, b"{}", "invalid_response"),
        (200, b"bad", "invalid_response"),
        (429, b"{}", "rate_limited"),
        (0, None, "timeout_or_connection"),
        (503, b"{}", "server_error"),
    ],
)
def test_retryable_failures_never_become_measurements(tmp_path, status, body, reason):
    ctx = context(tmp_path, [(status, body)])
    leg = hf_cohort.collect(ctx, {}, registry())
    assert leg["status"] == "partial"
    assert leg["retryable"][0]["reason"] == reason
    assert ctx.load("hf_cohort") == b""
    healthy = context(tmp_path, [(200, b'{"id":"a/x","downloads":0,"likes":0}')])
    retried = hf_cohort.collect(healthy, leg, registry())
    assert retried["stored"] == 1
    assert retried["attempt_history"][0]["reason"] == reason


def test_changed_identity_is_terminal_for_day_without_replacing_member(tmp_path):
    reg = registry(("a/x", "c/x"))
    before = json.dumps(reg, sort_keys=True)
    changed = dict(id="b/x", modelId="b/x", downloads=99, likes=10)
    ctx = context(tmp_path, [(200, json.dumps(changed).encode()), (503, b"{}")])
    initial = hf_cohort.collect(ctx, {}, reg)
    assert initial["status"] == "partial"
    assert initial["stored"] == 0
    assert initial["coverage"]["failed_by_reason"] == {
        "identity_mismatch": 1,
        "server_error": 1,
    }
    assert initial["failures"][0]["http_status"] == 200
    assert initial["failures"][0]["hf_id"] == "a/x"
    assert initial["failures"][0]["response_id"] == "b/x"
    assert initial["failures"][0]["response_ids"] == {"id": "b/x", "modelId": "b/x"}
    assert ctx.load("hf_cohort") == b""

    resumed = context(tmp_path, [(200, b'{"id":"c/x","downloads":1,"likes":0}')])
    result = hf_cohort.collect(resumed, json.loads(json.dumps(initial)), reg)
    assert resumed.http.calls == ["https://huggingface.co/api/models/c/x"]
    assert result["status"] == "ok"
    assert result["stored"] == 1
    assert result["coverage"]["coverage"] == 0.5
    assert result["coverage"]["attempted"] == 2
    assert result["coverage"]["failed_by_reason"] == {"identity_mismatch": 1}
    assert result["attempt_history"] == initial["retryable"]
    assert result["failures"] == initial["failures"]
    assert json.loads(resumed.load("hf_cohort"))["hf_id"] == "c/x"
    assert json.dumps(reg, sort_keys=True) == before

    same_day_rerun = context(tmp_path)
    saved = same_day_rerun.load("hf_cohort")
    assert hf_cohort.collect(same_day_rerun, result, reg)["status"] == "ok"
    assert not same_day_rerun.http.calls
    assert same_day_rerun.load("hf_cohort") == saved


def test_previous_retryable_identity_is_confirmed_then_closed_with_history(tmp_path):
    old_failure = dict(
        hf_id="a/x",
        attempted_at=NOW,
        measured_at=NOW,
        http_status=200,
        reason="identity_mismatch",
        response_id="b/x",
    )
    previous = dict(retryable=[old_failure], attempt_history=[dict(old_failure)])
    ctx = context(tmp_path, [(200, b'{"id":"b/x","downloads":2,"likes":1}')])
    result = hf_cohort.collect(ctx, previous, registry())
    assert len(ctx.http.calls) == 1
    assert result["status"] == "ok"
    assert result["retryable"] == []
    assert result["failures"][0]["response_id"] == "b/x"
    assert result["attempt_history"] == [old_failure, old_failure]
    assert result["coverage"]["coverage"] == 0
    assert ctx.load("hf_cohort") == b""


def test_identity_only_partial_day_closes_and_completed_rerun_is_noop(tmp_path):
    from collector import manifest as mf
    from collector.audit import check_day
    from collector.storage import read_json

    ctx = context(tmp_path, [(200, b'{"id":"b/x","downloads":2,"likes":1}')])
    write_json(registry_path(tmp_path, "fixed"), registry())
    manifest = mf.empty(DAY)
    manifest["status"] = "partial"
    for leg in mf.LEGS:
        if leg != "hf_cohort":
            manifest["legs"][leg] = dict(ctx.store(leg, b""), status="ok")
    manifest["legs"]["hf_cohort"] = dict(
        status="partial",
        retryable=[dict(hf_id="a/x", attempted_at=NOW, reason="identity_mismatch")],
    )
    path = tmp_path / "data/raw" / DAY / "_manifest.json"
    write_json(path, manifest)
    result = collect(tmp_path, DAY, ctx.http, ctx.now, cohort_id="fixed")
    assert result["status"] == "complete"
    assert result["legs"]["hf_cohort"]["coverage"]["coverage"] == 0
    assert len(ctx.http.calls) == 1
    assert read_json(path) == result
    assert not check_day(tmp_path, DAY)
    files = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    rerun = context(tmp_path)
    assert collect(tmp_path, DAY, rerun.http, rerun.now, cohort_id="fixed") == result
    assert not rerun.http.calls
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == files


def test_changed_identity_is_requested_again_on_next_utc_day(tmp_path):
    reg = registry()
    ctx = context(tmp_path, [(200, b'{"id":"b/x","downloads":2,"likes":1}')])
    previous = hf_cohort.collect(ctx, {}, reg)
    assert previous["status"] == "ok"
    later = Context(
        tmp_path,
        "2026-10-15",
        Http([(200, b'{"id":"a/x","downloads":3,"likes":1}')]),
        lambda: "2026-10-15T00:01:00Z",
    )
    result = hf_cohort.collect(later, previous, reg)
    assert len(later.http.calls) == 1
    assert result["stored"] == 1
    assert result["failures"] == []
    assert result["coverage"]["coverage"] == 1
    assert json.loads(later.load("hf_cohort"))["hf_id"] == "a/x"


@pytest.mark.parametrize(
    "body",
    [
        dict(id="a/x", modelId="b/x", downloads=1, likes=0),
        dict(id="b/x", modelId="c/x", downloads=1, likes=0),
        dict(id=123, downloads=1, likes=0),
        dict(id="../invalid", downloads=1, likes=0),
    ],
)
def test_inconsistent_or_invalid_identity_stays_retryable(tmp_path, body):
    ctx = context(tmp_path, [(200, json.dumps(body).encode())])
    result = hf_cohort.collect(ctx, {}, registry())
    assert result["status"] == "partial"
    assert result["failures"] == []
    assert result["retryable"][0]["reason"] == "identity_mismatch"
    assert ctx.load("hf_cohort") == b""


def test_changed_identity_response_after_midnight_stays_retryable(tmp_path):
    times = iter([NOW, NOW, "2026-10-15T00:00:01Z"])
    ctx = context(tmp_path, [(200, b'{"id":"b/x","downloads":2,"likes":1}')])
    ctx.now = lambda: next(times)
    result = hf_cohort.collect(ctx, {}, registry())
    assert result["status"] == "partial"
    assert result["failures"] == []
    assert result["retryable"][0]["reason"] == "identity_mismatch"
    assert ctx.load("hf_cohort") == b""


def test_no_historical_or_pre_enrollment_polling(tmp_path):
    ctx = context(tmp_path)
    ctx.snapshot_date = "2026-10-13"
    assert hf_cohort.collect(ctx, {}, registry())["status"] == "not_applicable"
    ctx.snapshot_date = DAY
    reg = registry()
    reg["members"][0]["enrolled_at"] = "2026-10-15T00:00:00Z"
    assert hf_cohort.collect(ctx, {}, reg)["coverage"]["coverage"] is None
    assert ctx.http.calls == []


def test_complete_snapshot_is_byte_identical(tmp_path):
    path = tmp_path / "data/raw" / DAY / "_manifest.json"
    write_json(
        path,
        {"schema_version": 1, "snapshot_date": DAY, "status": "complete", "legs": {}, "runs": []},
    )
    before = path.read_bytes()
    ctx = context(tmp_path)
    assert collect(tmp_path, DAY, ctx.http, ctx.now, cohort_id="missing")["status"] == "complete"
    assert path.read_bytes() == before
    assert ctx.http.calls == []


def test_silver_reads_partial_cohort_with_provenance(tmp_path):
    from silver.build import HF_LEGS, PRECEDENCE, SCHEMAS, _hf

    ctx = context(tmp_path, [(200, b'{"id":"a/x","downloads":1,"likes":0}')])
    hf_cohort.collect(ctx, {}, registry())
    errors = []
    rows = _hf(DAY, "hf_cohort", tmp_path / "data/raw" / DAY / "hf_cohort.jsonl.gz", errors)
    assert not errors
    assert rows[0]["cohort_id"] == "fixed"
    assert rows[0]["fetched_at"] == NOW
    assert HF_LEGS[-1] == "hf_cohort"
    assert PRECEDENCE["hf_cohort"] > PRECEDENCE["hf_new_models"]
    assert "cohort_id" in SCHEMAS["hf_repositories"]


def test_cutoff_excludes_future_fetches_and_creation(tmp_path):
    folder = tmp_path / "data/raw/2026-09-01"
    write_json(folder / "_manifest.json", {"legs": {"hf_top_models": {"status": "ok"}}})

    def body(rid, created):
        return dict(id=rid, downloads=1, likes=1, createdAt=created)

    raw = envelope(
        [body("a/old", "2026-08-01T00:00:00Z"), body("a/future", "2026-09-03T00:00:00Z")],
        "2026-09-01T00:00:00Z",
    )
    raw += envelope([body("a/lookahead", "2026-08-01T00:00:00Z")], "2026-09-04T00:00:00Z")
    write_gzip(folder / "hf_top_models.jsonl.gz", raw)
    result = initialize(tmp_path, "fixed", "2026-09-02T00:00:00Z", selected_at=NOW)
    assert [m["hf_repo_id"] for m in result["members"]] == ["a/old"]


def test_discovery_rename_and_naive_timestamp_are_not_reused(tmp_path):
    ctx = context(tmp_path, [(200, b'{"id":"a/x","downloads":2,"likes":1}')])
    ctx.store("hf_models", envelope(dict(id="a/x", downloads=99, likes=1), hf_id="old/x"))
    ctx.store("hf_top_models", envelope([dict(id="a/x", downloads=99, likes=1)], DAY + "T10:00:00"))
    leg = hf_cohort.collect(ctx, {}, registry())
    assert len(ctx.http.calls) == 1
    assert leg["coverage"]["observed_only_through_cohort"] == 1
    assert json.loads(ctx.load("hf_cohort"))["body"]["downloads"] == 2


def test_original_partial_manifest_does_not_enroll_cohort(tmp_path):
    from collector.manifest import load_or_empty

    path = tmp_path / "data/raw" / DAY / "_manifest.json"
    write_json(
        path,
        {"schema_version": 1, "snapshot_date": DAY, "status": "partial", "legs": {}, "runs": []},
    )
    assert load_or_empty(path, DAY)["legs"]["hf_cohort"]["status"] == "not_applicable"


def test_new_day_retries_previous_terminal_failure(tmp_path):
    ctx = context(tmp_path, [(200, b'{"id":"a/x","downloads":1,"likes":1}')])
    previous = {
        "failures": [
            {
                "hf_id": "a/x",
                "http_status": 404,
                "reason": "http_error",
                "attempted_at": "2026-10-13T10:00:00Z",
            }
        ]
    }
    assert hf_cohort.collect(ctx, previous, registry())["stored"] == 1
    assert len(ctx.http.calls) == 1


@pytest.mark.parametrize("leg", ["hf_top_models", "hf_new_models"])
def test_single_repository_envelopes_enroll_and_reuse(tmp_path, leg):
    body = dict(id="a/x", downloads=3, likes=1, createdAt="2026-09-01T00:00:00Z")
    ctx = context(tmp_path)
    ctx.store(leg, envelope(body, hf_id="a/x"))
    write_json(tmp_path / "data/raw" / DAY / "_manifest.json", {"legs": {leg: {"status": "ok"}}})
    enrolled = initialize(tmp_path, "fixed", NOW, selected_at=NOW)
    assert enrolled["members"][0]["hf_repo_id"] == "a/x"
    assert hf_cohort.collect(ctx, {}, enrolled)["stored"] == 1
    assert not ctx.http.calls


def test_current_date_guard_has_empty_coverage_and_no_writes(tmp_path):
    ctx = context(tmp_path)
    ctx.snapshot_date = "2026-10-13"
    result = hf_cohort.collect(ctx, {}, registry())
    assert result["coverage"] == dict(
        cohort_size=1,
        attempted=0,
        successful=0,
        failed_by_reason={},
        deferred=0,
        deferred_by_reason={},
        coverage=None,
        already_observed_through_discovery=0,
        observed_only_through_cohort=0,
    )
    assert not (tmp_path / "data").exists()


def test_midnight_preserves_earlier_success_and_stops_requests(tmp_path):
    times = iter([NOW, NOW, NOW, NOW, "2026-10-15T00:00:01Z", "2026-10-15T00:00:02Z"])
    ctx = context(
        tmp_path,
        [
            (200, b'{"id":"a/x","downloads":1,"likes":1}'),
            (200, b'{"id":"b/x","downloads":2,"likes":1}'),
        ],
    )
    ctx.now = lambda: next(times)
    result = hf_cohort.collect(ctx, {}, registry(("a/x", "b/x", "c/x")))
    assert len(ctx.http.calls) == 2
    assert json.loads(ctx.load("hf_cohort"))["hf_id"] == "a/x"
    assert result["status"] == "partial"
    assert {f["hf_id"] for f in result["retryable"]} == {"b/x"}
    assert result["deferred"] == [dict(hf_id="c/x", reason="snapshot_date_ended")]
    assert result["coverage"]["attempted"] == 2
    assert result["coverage"]["failed_by_reason"] == {"snapshot_date_ended": 1}
    assert result["coverage"]["deferred_by_reason"] == {"snapshot_date_ended": 1}
    assert all(f["reason"] == "snapshot_date_ended" for f in result["retryable"])
    assert result["retryable"][0]["http_status"] == 200
    assert result["retryable"][0]["attempts"] == 1
    assert result["coverage"]["successful"] == 1
    assert ctx.http.min_interval == 0.25


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"cohort_id":"fixed","members":[]}',
        b'{"cohort_id":"fixed","selected_at":"2026-10-14T00:00:00Z","members":[{}]}',
    ],
)
def test_invalid_registry_retains_completed_legs(tmp_path, content):
    from collector import manifest as mf

    reg_path = registry_path(tmp_path, "fixed")
    reg_path.parent.mkdir(parents=True)
    reg_path.write_bytes(content)
    manifest = mf.empty(DAY)
    for leg in mf.LEGS:
        if leg != "hf_cohort":
            manifest["legs"][leg] = {"status": "ok", "stored": 7}
    mf.save(tmp_path / "data/raw" / DAY / "_manifest.json", manifest)
    ctx = context(tmp_path)
    result = collect(tmp_path, DAY, ctx.http, ctx.now, cohort_id="fixed")
    assert result["status"] == "partial"
    assert result["legs"]["hf_cohort"]["failure_kind"] == "invalid_registry"
    assert "offline before collection" in result["legs"]["hf_cohort"]["reason"]
    assert "failures" not in result["legs"]["hf_cohort"]
    assert result["legs"]["hf_models"] == {"status": "ok", "stored": 7}
    assert not ctx.http.calls


@pytest.mark.parametrize("leg", ["hf_top_models", "hf_new_models"])
def test_single_repository_identity_mismatch_is_not_enrolled_or_reused(tmp_path, leg):
    ctx = context(tmp_path, [(200, b'{"id":"a/x","downloads":1,"likes":1}')])
    ctx.store(leg, envelope(dict(id="a/x", downloads=99, likes=1), hf_id="old/x"))
    write_json(tmp_path / "data/raw" / DAY / "_manifest.json", {"legs": {leg: {"status": "ok"}}})
    with pytest.raises(ValueError, match="no eligible"):
        initialize(tmp_path, "fixed", NOW, selected_at=NOW)
    assert hf_cohort.collect(ctx, {}, registry())["stored"] == 1
    assert len(ctx.http.calls) == 1
    assert json.loads(ctx.load("hf_cohort"))["body"]["downloads"] == 1


def test_date_change_before_first_missing_request_stops_network(tmp_path):
    ctx = context(tmp_path)
    ctx.store("hf_models", envelope(dict(id="a/x", downloads=1, likes=1), hf_id="a/x"))
    times = iter([NOW, "2026-10-15T00:00:00Z"])
    ctx.now = lambda: next(times)
    result = hf_cohort.collect(ctx, {}, registry(("a/x", "b/x")))
    assert not ctx.http.calls
    assert result["stored"] == 1
    assert result["retryable"] == []
    assert result["deferred"] == [dict(hf_id="b/x", reason="snapshot_date_ended")]
    assert result["coverage"]["attempted"] == 1
    assert result["coverage"]["failed_by_reason"] == {}
    assert result["coverage"]["deferred"] == 1
    assert result["coverage"]["coverage"] == 0.5


def test_http_diagnostics_survive_retry_history(tmp_path):
    ctx = context(tmp_path)
    ctx.http.get = lambda url: Response(
        url, 0, error="connection timed out after retries", attempts=3
    )
    result = hf_cohort.collect(ctx, {}, registry())
    failure = result["retryable"][0]
    assert failure["error"] == "connection timed out after retries"
    assert failure["attempts"] == 3
    retry = context(tmp_path, [(200, b'{"id":"a/x","downloads":1,"likes":1}')])
    recovered = hf_cohort.collect(retry, result, registry())
    assert recovered["stored"] == 1
    assert recovered["attempt_history"] == [failure]


def test_elapsed_budget_saves_results_and_resumes_only_unresolved(tmp_path, monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(hf_cohort.time, "monotonic", lambda: elapsed[0])
    reg = registry(("a/x", "b/x", "c/x", "d/x", "e/x"))
    ctx = context(tmp_path)
    responses = iter(
        [
            Response("url", 200, b'{"id":"a/x","downloads":1,"likes":0}'),
            Response("url", 404, b"{}"),
            Response("url", 503, b"{}", attempts=3),
        ]
    )

    def get(url):
        ctx.http.calls.append(url)
        response = next(responses)
        elapsed[0] += 601
        return response

    ctx.http.get = get
    result = hf_cohort.collect(ctx, {}, reg)
    assert len(ctx.http.calls) == 3
    assert result["status"] == "partial"
    assert result["stored"] == 1
    assert result["coverage"]["attempted"] == 3
    assert result["coverage"]["coverage"] == 0.2
    assert result["coverage"]["deferred"] == 2
    assert result["coverage"]["deferred_by_reason"] == {"budget_exhausted": 2}
    assert result["deferred"] == [
        dict(hf_id=rid, reason="budget_exhausted") for rid in ("d/x", "e/x")
    ]
    retry = context(
        tmp_path,
        [
            (200, json.dumps(dict(id=rid, downloads=0, likes=0)).encode())
            for rid in ("c/x", "d/x", "e/x")
        ],
    )
    recovered = hf_cohort.collect(retry, result, reg)
    assert [url.rsplit("/", 2)[-2:] for url in retry.http.calls] == [
        ["c", "x"],
        ["d", "x"],
        ["e", "x"],
    ]
    assert recovered["status"] == "ok"
    assert recovered["stored"] == 4
    assert recovered["deferred"] == []
    assert recovered["attempt_history"] == result["retryable"]
    assert recovered["failures"] == result["failures"]


def test_ongoing_call_can_finish_after_elapsed_budget(tmp_path, monkeypatch):
    ticks = iter([0, 1799, 1801])
    monkeypatch.setattr(hf_cohort.time, "monotonic", lambda: next(ticks))
    ctx = context(tmp_path, [(200, b'{"id":"a/x","downloads":1,"likes":0}')])
    result = hf_cohort.collect(ctx, {}, registry(("a/x", "b/x")))
    assert result["stored"] == 1
    assert result["deferred"] == [dict(hf_id="b/x", reason="budget_exhausted")]
    assert len(ctx.http.calls) == 1


@pytest.mark.parametrize(
    "malformation", ["identity", "duplicate", "active", "version", "bool_version"]
)
def test_configured_registry_identity_and_shape_rejected_before_polling(tmp_path, malformation):
    from collector import manifest as mf

    reg = registry()
    if malformation == "identity":
        reg["cohort_id"] = "other"
    elif malformation == "duplicate":
        reg["members"].append(dict(reg["members"][0]))
    elif malformation == "active":
        reg["members"][0]["active"] = "false"
    else:
        reg["selection_version"] = True if malformation == "bool_version" else 999
    write_json(registry_path(tmp_path, "fixed"), reg)
    manifest = mf.empty(DAY)
    for leg in mf.LEGS:
        if leg != "hf_cohort":
            manifest["legs"][leg] = {"status": "ok", "stored": 7}
    mf.save(tmp_path / "data/raw" / DAY / "_manifest.json", manifest)
    ctx = context(tmp_path)
    result = collect(tmp_path, DAY, ctx.http, ctx.now, cohort_id="fixed")
    assert result["legs"]["hf_cohort"]["failure_kind"] == "invalid_registry"
    assert result["legs"]["hf_models"] == {"status": "ok", "stored": 7}
    assert not ctx.http.calls


def test_scheduler_environment_can_collect_the_v2_registry_offline(tmp_path, monkeypatch):
    from pathlib import Path

    from conftest import FakeHttp

    from collector import __main__ as cli

    cohort_id = "hf_lifecycle_20261015_v2"
    source = Path(__file__).resolve().parents[1] / "data/cohorts" / (cohort_id + ".json")
    reg = json.loads(source.read_bytes())
    destination = registry_path(tmp_path, cohort_id)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(source.read_bytes())
    http = FakeHttp()
    monkeypatch.setenv("HF_COHORT_ID", cohort_id)
    monkeypatch.setattr(cli, "HttpClient", lambda **kwargs: http)
    monkeypatch.setattr(
        cli,
        "collect",
        lambda root, **kwargs: collect(root, now=lambda: reg["selected_at"], **kwargs),
    )
    day = reg["selected_at"][:10]
    assert cli.main(["--root", str(tmp_path), "--date", day]) == 0
    manifest = json.loads((tmp_path / "data/raw" / day / "_manifest.json").read_bytes())
    leg = manifest["legs"]["hf_cohort"]
    assert leg["cohort_id"] == cohort_id
    assert leg["requested"] == leg["stored"] == 1500
