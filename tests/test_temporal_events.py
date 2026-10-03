"""Events derive only attested positive changes, independent of optional canonicals."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from collector.storage import write_gzip, write_json
from temporal.__main__ import main
from temporal.archive import fingerprint, write_new_json
from temporal.baselines import ORIGINAL_BASELINE
from temporal.deployments import build_deployment_layer, deployment_key, stable_hash
from temporal.events import DEFERRED, ENABLED, build_events, validate_identity
from temporal.identity import build_identity_history
from temporal.index import build_index
from temporal.portability import build_proof

RECORDED = "2026-09-30T12:00:00Z"


def fact(day="2026-09-10", provider="one", repo="HF/A", number=0, price="0.1", context=8000,
         parameters=None, features=None, at="default", source="hf_inference_providers", scope=None):
    at = f"{day}T02:00:00Z" if at == "default" else at
    row = {"deployment_key": deployment_key(provider, "glm"), "provider": provider,
           "provider_model_id": "glm", "snapshot_id": f"{day}:{source}",
           "source": source, "source_scope": scope or {}, "source_hf_repo_id": repo,
           "presence_state": "PRESENT", "observed_at": at, "observation_date": day,
           "observed_precision": "second" if at else "day",
           "price": {"values": {"input": price} if price is not None else None,
                     "currency": None, "unit": None, "source_field": "pricing"},
           "context": {"context_length": context, "max_completion_tokens": None,
                       "source_field": "context"},
           "capabilities": {"supported_parameters": parameters, "features": features},
           "source_evidence": {"row_locator": str(number), "snapshot_path": f"{day}/source.gz",
                               "sha256_file": "fixture", "sha256_raw": "fixture"}}
    row["observation_id"] = stable_hash([day, provider, number, source, scope, repo, at])
    return row


def layer(rows):
    return {"deployment_observations": rows, "recorded_at": RECORDED,
            "summary": {"source_cutoffs": {"hf_inference_providers": "2026-09-12"}}}


def replay(rows, ledger=None, recorded_at=RECORDED):
    observations = layer(rows)
    history = build_identity_history(observations, ledger, RECORDED)
    return build_events(observations, history, recorded_at)


def changed(result):
    return [event for event in result["lifecycle_events"] if event["event_type"] in (
        "PRICE_CHANGED", "CONTEXT_CHANGED", "CAPABILITY_CHANGED")]


def test_policy_enabled_and_deferred_types_are_explicit_with_reasons():
    result = replay([fact(), fact("2026-09-11")])
    assert set(result["enabled_event_types"]) == set(ENABLED)
    assert result["deferred_event_types"] == DEFERRED
    assert all(DEFERRED.values())
    assert set(result["summary"]["events_by_type"]) == {"PROVIDER_LISTED", "HF_IDENTITY_LINKED"}
    assert len(result["lifecycle_events"]) == 2
    listed = next(row for row in result["lifecycle_events"]
                  if row["event_type"] == "PROVIDER_LISTED")
    assert listed["left_censored"] and listed["effective_at"] is None


def test_price_context_and_capability_diffs_need_no_canonical_identity():
    first = fact(repo=None, parameters=["tools"], features={"toolCalling": False})
    second = fact("2026-09-11", repo=None, price="0.2", context=16000,
                  parameters=["tools", "reasoning"], features={"toolCalling": True})
    events = changed(replay([first, second]))
    assert {row["event_type"] for row in events} == {
        "PRICE_CHANGED", "CONTEXT_CHANGED", "CAPABILITY_CHANGED"}
    assert all(row["canonical_model_id"] is None for row in events)
    for event in events:
        assert event["previous_observation_ids"] == [first["observation_id"]]
        assert event["observation_ids"] == [second["observation_id"]]
        assert event["previous_observed_at"] == first["observed_at"]
        assert len(event["source_evidence"]) == 2 and event["effective_at"] is None
    price = next(row for row in events if row["event_type"] == "PRICE_CHANGED")
    assert price["previous_value"]["values"]["input"] == "0.1"
    assert price["new_value"]["values"]["input"] == "0.2"


@pytest.mark.parametrize("old,new", [("0.10", "0.1"), ("1e2", 100), ("-0.00", "0"),
                                      ("0.1", 0.1), (0, "0.000")])
def test_price_comparison_ignores_decimal_formatting_without_losing_raw_values(old, new):
    assert not changed(replay([fact(price=old), fact("2026-09-11", price=new)]))


def test_capability_sets_ignore_order_and_false_is_not_numeric_zero():
    a = fact(parameters=["tools", "reasoning"], features={"x": False})
    b = fact("2026-09-11", parameters=["reasoning", "tools"], features={"x": False})
    assert not changed(replay([a, b]))
    b["capabilities"]["features"]["x"] = 0
    assert changed(replay([a, b]))[0]["event_type"] == "CAPABILITY_CHANGED"


def test_unknown_values_break_comparison_and_new_unknown_fields_do_not_imply_change():
    rows = [fact(parameters=["tools"]), fact("2026-09-11", price=None, context=None),
            fact("2026-09-12", price="9", context=16000)]
    assert not changed(replay(rows))
    a, b = fact(features=None), fact("2026-09-11", features={"toolCalling": True})
    assert not changed(replay([a, b]))


def test_scopes_sources_and_provider_namespaces_are_never_cross_compared():
    rows = [fact(), fact("2026-09-11", provider="two", price="9"),
            fact("2026-09-11", source="openrouter_models", price="9"),
            fact("2026-09-12", scope={"filter": "bounded"}, price="9")]
    result = replay(rows)
    assert not changed(result)
    assert result["summary"]["events_by_type"]["PROVIDER_LISTED"] == 2


def test_absence_unknown_and_gaps_produce_no_delisting_reappearance_or_second_listing():
    missing = fact("2026-09-11", price="9")
    missing["presence_state"] = "UNKNOWN"
    rows = [fact(), missing, fact("2026-09-12", price="0.2")]
    result = replay(rows)
    assert result["summary"]["events_by_type"]["PROVIDER_LISTED"] == 1
    assert result["summary"]["events_by_type"]["PRICE_CHANGED"] == 1
    assert set(result["summary"]["events_by_type"]) <= set(ENABLED)
    assert result["summary"]["absence_events_generated"] == 0
    # Removing a deployment from a later catalogue supplies no transition row.
    assert len(replay([fact()])["lifecycle_events"]) == 2


def test_conflicting_snapshot_rows_reset_only_the_ambiguous_comparison_field():
    rows = [fact(), fact("2026-09-11", price="0.2", context=16000),
            fact("2026-09-11", number=1, repo="HF/B", price="0.3", context=16000),
            fact("2026-09-12", price="0.4", context=16000)]
    result = replay(rows)
    assert result["summary"]["ambiguous_comparison_batches_by_field"] == {"price": 1}
    assert [row["event_type"] for row in changed(result)] == ["CONTEXT_CHANGED"]
    event = changed(result)[0]
    assert event["identity_state"] == "ambiguous" and event["hf_repo_id"] is None


def test_equal_duplicate_price_rows_supply_all_evidence_without_duplicate_events():
    rows = [fact(), fact("2026-09-11", price="0.2"),
            fact("2026-09-11", number=1, repo="HF/B", price="0.20")]
    result = replay(rows)
    event = changed(result)[0]
    assert event["event_type"] == "PRICE_CHANGED" and len(event["observation_ids"]) == 2
    assert len(changed(result)) == 1


def test_date_only_first_presence_is_not_midnight_and_breaks_unknown_day_comparisons():
    rows = [fact(), fact("2026-09-11", price="0.2", at=None),
            fact("2026-09-11", number=1, price="0.3"), fact("2026-09-12", price="0.4")]
    assert not changed(replay(rows))
    result = replay([fact(at=None), fact("2026-09-11")])
    listed = next(row for row in result["lifecycle_events"]
                  if row["event_type"] == "PROVIDER_LISTED")
    assert listed["observed_at"] is None and listed["observed_precision"] == "day"
    assert listed["observed_basis"] == "source manifest day"


def test_identity_link_revision_conflict_and_resolution_are_belief_transitions():
    rows = [fact(), fact("2026-09-11", repo="HF/B"),
            fact("2026-09-11", number=1, repo="HF/A"), fact("2026-09-12", repo="HF/B")]
    events = [row for row in replay(rows)["lifecycle_events"] if row["field"] == "hf_identity"]
    assert [row["event_type"] for row in events] == [
        "HF_IDENTITY_LINKED", "HF_IDENTITY_REVISED", "HF_IDENTITY_REVISED"]
    assert events[1]["new_value"]["state"] == "ambiguous"
    assert events[2]["previous_value"]["state"] == "ambiguous"
    assert events[2]["new_value"]["hf_repo_id"] == "HF/B"
    assert all(row["identity_decision_id"] for row in events)


def test_initial_ambiguity_produces_no_hf_event_until_first_accepted_link():
    result = replay([fact(), fact(number=1, repo="HF/B"), fact("2026-09-11", repo="HF/A")])
    events = [row for row in result["lifecycle_events"] if row["field"] == "hf_identity"]
    assert len(events) == 1 and events[0]["event_type"] == "HF_IDENTITY_LINKED"


def test_canonical_only_review_does_not_emit_hf_revision_and_fuzzy_produces_no_link():
    row = fact()
    entry = {"observation_id": row["observation_id"], "mapping_method": "curated",
             "hf_repo_id": "HF/A", "canonical_model_id": "canonical:reviewed",
             "review_status": "approved", "reviewed_by": "reviewer", "review_reason": "reference",
             "evidence_available_at": "2026-09-11T03:00:00Z", "evidence_reference": "review"}
    result = replay([row], {"schema_version": 1, "candidates": [entry]})
    assert result["summary"]["events_by_type"] == {"PROVIDER_LISTED": 1, "HF_IDENTITY_LINKED": 1}
    assert all(event["canonical_model_id"] is None for event in result["lifecycle_events"])
    entry["mapping_method"] = "fuzzy"
    row["source_hf_repo_id"] = None
    assert replay([row], {"schema_version": 1, "candidates": [entry]})["summary"][
        "events_by_type"] == {"PROVIDER_LISTED": 1}


def test_event_ids_order_and_payloads_are_deterministic_across_replays_and_row_order():
    rows = [fact(), fact("2026-09-11", price="0.2", repo="HF/B")]
    a = replay(rows)
    b = replay(list(reversed(rows)), recorded_at="2026-10-04T12:00:00Z")
    assert [row["event_id"] for row in a["lifecycle_events"]] == [
        row["event_id"] for row in b["lifecycle_events"]]
    assert len({row["event_id"] for row in a["lifecycle_events"]}) == len(a["lifecycle_events"])
    assert a["recorded_at"] != b["recorded_at"]


@pytest.mark.parametrize("field", ["identity_links", "identity_decisions", "identity_candidates"])
def test_altered_identity_history_is_rejected_by_reconstruction(field):
    observations = layer([fact()])
    history = build_identity_history(observations, recorded_at=RECORDED)
    history[field] = []
    with pytest.raises(ValueError, match="altered"):
        validate_identity(observations, history)


@pytest.fixture
def archived(tmp_path):
    for day, price in (("2026-09-10", "0.1"), ("2026-09-11", "0.2")):
        folder = tmp_path / "data/raw" / day
        stored = write_gzip(folder / "openrouter_models.json.gz", json.dumps({"data": [
            {"id": "vendor/glm", "hugging_face_id": "HF/GLM", "pricing": {"prompt": price}}
        ]}).encode())
        manifest = {"snapshot_date": day, "status": "complete", "legs": {
            "openrouter_models": stored | {"status": "ok", "fetched_at": f"{day}T02:00:00Z"}}}
        write_json(folder / "_manifest.json", manifest)
        path = folder / "_manifest.json"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    baseline = fingerprint(tmp_path)
    write_new_json(tmp_path, ORIGINAL_BASELINE, baseline)
    build_proof(tmp_path, baseline)
    observations = build_deployment_layer(tmp_path, build_index(tmp_path), RECORDED)
    history = build_identity_history(observations, recorded_at=RECORDED)
    write_new_json(tmp_path, Path("data/temporal_v2/deployments.json"), observations)
    write_new_json(tmp_path, Path("data/temporal_v2/identity.json"), history)
    return tmp_path


def cli_args(root):
    return ["--root", str(root), "events", "--observations", "data/temporal_v2/deployments.json",
            "--identity", "data/temporal_v2/identity.json"]


def test_cli_dry_run_output_protection_and_lf_input_evidence(archived):
    root = archived
    for path in (root / "data/raw").rglob("_manifest.json"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    before = fingerprint(root)["files"]
    args = cli_args(root)
    output = root / "data/temporal_v2/lifecycle_events.json"
    assert main([*args, "--dry-run"]) == 0 and not output.exists()
    assert main(args) == 0
    saved = output.read_bytes()
    assert main(args) == 1 and output.read_bytes() == saved
    assert main([*args, "--output", "data/raw/events.json"]) == 1
    assert fingerprint(root)["files"] == before
    result = json.loads(saved)
    assert result["summary"]["events_by_type"]["PRICE_CHANGED"] == 1


@pytest.mark.parametrize("target", ["archive", "input"])
def test_publication_rejects_racing_source_and_input_changes(archived, monkeypatch, target):
    import temporal.__main__ as cli

    root = archived
    original = cli.build_events

    def mutate(observations, history):
        result = original(observations, history)
        path = root / ("data/raw/2026-09-10/_manifest.json" if target == "archive"
                       else "data/temporal_v2/identity.json")
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(cli, "build_events", mutate)
    assert main(cli_args(root)) == 1
    assert not (root / "data/temporal_v2/lifecycle_events.json").exists()


def test_different_deployment_input_cannot_borrow_identity_history():
    observations = layer([fact()])
    history = build_identity_history(observations, recorded_at=RECORDED)
    different = copy.deepcopy(observations)
    different["deployment_observations"][0]["price"]["values"]["input"] = "9"
    with pytest.raises(ValueError, match="different deployment"):
        validate_identity(different, history)


def test_review_ledger_is_required_and_later_identity_cannot_relabel_earlier_events():
    row = fact(repo=None)
    observations = layer([row])
    ledger = {"schema_version": 1, "candidates": [{
        "observation_id": row["observation_id"], "mapping_method": "exact_external_identifier",
        "hf_repo_id": "HF/A", "evidence_available_at": "2026-09-11T03:00:00Z",
        "evidence_reference": "identifier/fixture", "review_status": "approved",
        "reviewed_by": "reviewer", "review_reason": "external identifier checked",
    }]}
    history = build_identity_history(observations, ledger, RECORDED)
    with pytest.raises(ValueError, match="ledger"):
        validate_identity(observations, history)
    validate_identity(observations, history, ledger)
    result = build_events(observations, history, RECORDED)
    listed = next(event for event in result["lifecycle_events"]
                  if event["event_type"] == "PROVIDER_LISTED")
    linked = next(event for event in result["lifecycle_events"]
                  if event["event_type"] == "HF_IDENTITY_LINKED")
    assert listed["hf_repo_id"] is None
    assert linked["observed_at"] == "2026-09-11T03:00:00+00:00"
