"""Identity decisions preserve conflicts, evidence priority and observed-time history."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from collector.storage import write_gzip, write_json
from temporal.__main__ import main
from temporal.archive import fingerprint, write_new_json
from temporal.baselines import ORIGINAL_BASELINE
from temporal.deployments import build_deployment_layer, deployment_key
from temporal.identity import build_identity_history, identity_at, validate_observations
from temporal.index import build_index
from temporal.portability import build_proof

RECORDED = "2026-10-03T12:00:00+00:00"


def fact(repo="HF/A", day="2026-09-10", number=0, provider="one", at=None, snapshot=None):
    return {"observation_id": f"{provider}:{day}:{number}",
            "snapshot_id": snapshot or day, "deployment_key": deployment_key(provider, "glm"),
            "provider": provider, "provider_model_id": "glm", "source": "hf_inference_providers",
            "source_hf_repo_id": repo, "observed_at": at or f"{day}T02:00:00+00:00",
            "observed_precision": "second", "source_evidence": {"row_locator": str(number)}}


def layer(*rows):
    return {"deployment_observations": list(rows),
            "summary": {"source_cutoffs": {"hf_inference_providers": "2026-09-12"}}}


def evidence(row, method="curated", repo="HF/B", at="2026-09-10T03:00:00+00:00",
             canonical=None, status="approved"):
    return {"observation_id": row["observation_id"], "mapping_method": method,
            "hf_repo_id": repo, "canonical_model_id": canonical,
            "evidence_available_at": at, "evidence_reference": "review/fixture",
            "review_status": status, "reviewed_by": "fixture-reviewer",
            "review_reason": "exact source identifier inspected"}


def replay(rows, *entries):
    return build_identity_history(layer(*rows), {"schema_version": 1, "candidates": list(entries)},
                                  recorded_at=RECORDED)


def test_explicit_mapping_has_priority_and_never_assigns_canonical():
    row = fact()
    result = replay([row], evidence(row, "exact_external_identifier"),
                    evidence(row, "exact_deterministic_correspondence"), evidence(row),
                    evidence(row, "fuzzy", canonical="canonical:wrong"))
    assert {item["hf_repo_id"] for item in result["identity_links"]} == {"HF/A"}
    assert all(item["canonical_model_id"] is None for item in result["identity_links"])
    assert result["summary"]["events_generated"] == 0
    fuzzy = next(item for item in result["identity_candidates"]
                 if item["mapping_method"] == "fuzzy")
    assert not fuzzy["eligible_for_link"]


@pytest.mark.parametrize("methods,winner", [
    (["curated", "exact_deterministic_correspondence", "exact_external_identifier"],
     "exact_external_identifier"),
    (["curated", "exact_deterministic_correspondence"], "exact_deterministic_correspondence"),
    (["curated", "fuzzy"], "curated"),
])
def test_reviewed_evidence_priority_is_deterministic(methods, winner):
    row = fact(repo=None)
    entries = [evidence(row, method, repo="HF/" + method) for method in methods]
    result = replay([row], *entries)
    assert result["identity_links"][-1]["mapping_method"] == winner
    assert result["identity_links"][-1]["hf_repo_id"] == "HF/" + winner


def test_fuzzy_and_pending_evidence_remain_candidates_even_after_review():
    row = fact(repo=None)
    result = replay([row], evidence(row, "fuzzy", canonical="canonical:glm"),
                    evidence(row, status="pending"))
    assert len(result["identity_candidates"]) == 2
    assert result["identity_links"] == []
    assert result["summary"]["canonical_assignments"] == 0


def test_glm_collision_is_ambiguous_across_pages_of_the_same_snapshot():
    first = fact("HF/GLM")
    second = fact("HF/GLM-FP8", number=1, at="2026-09-10T03:00:00+00:00")
    result = replay([first, second])
    assert len(result["identity_candidates"]) == 2 and result["identity_links"] == []
    assert result["identity_decisions"][0]["state"] == "ambiguous"
    key = first["deployment_key"]
    assert identity_at(result, key, "2026-09-10T02:30:00Z")["state"] == "unknown"
    decision = identity_at(result, key, "2026-09-10T03:00:00Z")
    assert decision["state"] == "ambiguous" and len(decision["evidence"]) == 2


def test_conflict_closes_old_link_and_later_mapping_resolves_without_overwriting_history():
    a = fact()
    conflict = [fact("HF/A", day="2026-09-11"), fact("HF/B", day="2026-09-11", number=1)]
    b = fact("HF/B", day="2026-09-12")
    result = replay([a, *conflict, b])
    key = a["deployment_key"]
    assert [row["state"] for row in result["identity_decisions"]] == [
        "resolved", "ambiguous", "resolved"]
    assert result["identity_links"][0]["valid_to"] == conflict[0]["observed_at"]
    assert identity_at(result, key, "2026-09-11T01:59:59Z")["hf_repo_id"] == "HF/A"
    assert identity_at(result, key, "2026-09-11T02:00:00Z")["state"] == "ambiguous"
    assert identity_at(result, key, "2026-09-12T02:00:00Z")["hf_repo_id"] == "HF/B"
    assert identity_at(result, key, "2026-09-12T02:00:00Z",
                       "2026-09-13T00:00:00Z")["state"] == "not_recorded"


def test_same_instant_conflicts_are_merged_and_input_order_does_not_pick_a_winner():
    a, b = fact(), fact("HF/B", number=1, snapshot="other-snapshot")
    forward, backward = replay([a, b]), replay([b, a])
    for field in ("identity_candidates", "identity_decisions", "identity_links"):
        assert forward[field] == backward[field]
    assert forward["identity_links"] == []
    assert len(forward["identity_decisions"]) == 1


def test_namespaces_remain_separate_and_missing_mapping_does_not_retract_prior_belief():
    a, b = fact(), fact("HF/B", provider="two")
    unknown = fact(None, day="2026-09-11")
    result = replay([a, b, unknown])
    assert identity_at(result, a["deployment_key"], "2026-09-12T00:00:00Z")["hf_repo_id"] == "HF/A"
    assert identity_at(result, b["deployment_key"], "2026-09-12T00:00:00Z")["hf_repo_id"] == "HF/B"


def test_canonical_assignment_requires_review_matches_repo_and_tracks_corrections():
    row = fact()
    result = replay([row], evidence(row, repo="HF/A", canonical="canonical:first"),
                    evidence(row, repo="HF/A", canonical="canonical:corrected",
                             at="2026-09-11T03:00:00+00:00"))
    key = row["deployment_key"]
    assert identity_at(result, key, "2026-09-10T02:00:00Z")["canonical_model_id"] is None
    first = identity_at(result, key, "2026-09-10T03:00:00Z")
    corrected = identity_at(result, key, "2026-09-11T03:00:00Z")
    assert first["canonical_model_id"] == "canonical:first"
    assert corrected["canonical_model_id"] == "canonical:corrected"
    mismatch = replay([row], evidence(row, repo="HF/B", canonical="canonical:wrong"))
    assert mismatch["identity_links"][-1]["canonical_model_id"] is None


def test_conflicting_curated_canonicals_do_not_break_explicit_hf_link():
    row = fact()
    result = replay([row], evidence(row, repo="HF/A", canonical="canonical:a"),
                    evidence(row, repo="HF/A", canonical="canonical:b"))
    assert result["identity_links"][-1]["hf_repo_id"] == "HF/A"
    assert result["identity_links"][-1]["canonical_model_id"] is None
    assert result["identity_links"][-1]["canonical_status"] == "ambiguous"


def test_day_only_and_invalid_repo_are_candidates_without_invented_history():
    day_only = fact()
    day_only["observed_at"] = None
    day_only["observed_precision"] = "day"
    result = replay([day_only])
    assert result["identity_candidates"][0]["observed_at"] is None
    assert result["identity_links"] == [] and result["summary"]["untimed_batches"] == 1
    assert replay([fact("not-a-qualified-repo")])["identity_links"] == []


def test_ids_are_repeatable_while_recorded_time_tracks_materialization():
    rows = [fact(), fact("HF/B", day="2026-09-11")]
    a = replay(rows)
    b = build_identity_history(layer(*rows), recorded_at="2026-10-04T12:00:00Z")
    for field, id_field in (("identity_candidates", "candidate_id"),
                            ("identity_links", "identity_link_id"),
                            ("identity_decisions", "decision_id")):
        assert [row[id_field] for row in a[field]] == [row[id_field] for row in b[field]]
    assert a["recorded_at"] != b["recorded_at"]


@pytest.mark.parametrize("change,match", [
    ({"mapping_method": "explicit_provider_mapping"}, "protected source"),
    ({"reviewed_by": None}, "reviewer"),
    ({"evidence_available_at": "2026-09-09T00:00:00Z"}, "precede"),
    ({"mapping_method": "exact_external_identifier", "canonical_model_id": "x"}, "curated"),
    ({"evidence_available_at": "2026-10-04T00:00:00Z"}, "future"),
])
def test_review_contract_rejects_unsubstantiated_or_backdated_evidence(change, match):
    row = fact()
    with pytest.raises(ValueError, match=match):
        replay([row], {**evidence(row), **change})


@pytest.fixture
def archived(tmp_path):
    folder = tmp_path / "data/raw/2026-09-10"
    stored = write_gzip(folder / "openrouter_models.json.gz", json.dumps({"data": [
        {"id": "vendor/glm", "hugging_face_id": "HF/GLM"}]}).encode())
    manifest_data = {"snapshot_date": "2026-09-10", "status": "complete",
                     "legs": {"openrouter_models": stored | {
                         "status": "ok", "fetched_at": "2026-09-10T02:00:00Z"}}}
    write_json(folder / "_manifest.json", manifest_data)
    manifest = folder / "_manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
    baseline = fingerprint(tmp_path)
    write_new_json(tmp_path, ORIGINAL_BASELINE, baseline)
    build_proof(tmp_path, baseline)
    result = build_deployment_layer(tmp_path, build_index(tmp_path), RECORDED)
    return tmp_path, baseline, result


def test_archived_provenance_validates_original_crlf_evidence_on_lf_checkout(archived):
    root, baseline, result = archived
    manifest = root / "data/raw/2026-09-10/_manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b"\r\n", b"\n"))
    before = fingerprint(root)["files"]
    validate_observations(root, result, baseline)
    build_identity_history(result)
    assert fingerprint(root)["files"] == before


@pytest.mark.parametrize("alteration", ["repo", "time", "row_hash", "omission", "manifest_path"])
def test_altered_deployment_facts_cannot_supply_identity_evidence(archived, alteration):
    root, baseline, result = archived
    result = copy.deepcopy(result)
    row = result["deployment_observations"][0]
    if alteration == "repo":
        row["source_hf_repo_id"] = "HF/False"
    elif alteration == "time":
        row["observed_at"] = "2026-09-09T00:00:00Z"
    elif alteration == "row_hash":
        row["source_evidence"]["source_row_sha256"] = "0" * 64
    elif alteration == "manifest_path":
        row["source_evidence"]["manifest_path"] = "../outside"
    else:
        result["deployment_observations"] = []
    with pytest.raises(ValueError):
        validate_observations(root, result, baseline)


def test_identity_cli_dry_run_exclusive_output_and_query(archived, capsys):
    root, _, result = archived
    source = Path("data/temporal_v2/deployments.json")
    write_new_json(root, source, result)
    args = ["--root", str(root), "identity", "--observations", str(source)]
    before = fingerprint(root)["files"]
    assert main([*args, "--dry-run"]) == 0
    output = root / "data/temporal_v2/identity_history.json"
    assert not output.exists()
    assert main(args) == 0
    saved = output.read_bytes()
    assert main(args) == 1 and output.read_bytes() == saved
    assert main([*args, "--output", "data/raw/identity.json"]) == 1
    assert main(["--root", str(root), "identity-at", "--history", str(output),
                 "--deployment-key", deployment_key("openrouter", "vendor/glm"),
                 "--at", "2026-09-10T03:00:00Z"]) == 0
    assert fingerprint(root)["files"] == before
    assert "HF/GLM" in capsys.readouterr().out


def test_identity_publication_detects_changes_to_input_during_replay(archived, monkeypatch):
    import temporal.__main__ as cli

    root, _, result = archived
    source = Path("data/temporal_v2/deployments.json")
    write_new_json(root, source, result)
    original = cli.build_identity_history

    def mutate(layer, ledger):
        history = original(layer, ledger)
        (root / source).write_bytes(b"{}")
        return history

    monkeypatch.setattr(cli, "build_identity_history", mutate)
    assert main(["--root", str(root), "identity", "--observations", str(source)]) == 1
    assert not (root / "data/temporal_v2/identity_history.json").exists()
