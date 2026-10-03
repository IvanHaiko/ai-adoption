"""Common observation structure preserves provider scope, timestamps and source facts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.storage import write_gzip, write_json
from temporal.__main__ import main
from temporal.archive import fingerprint, verify, write_new_json
from temporal.baselines import ORIGINAL_BASELINE
from temporal.deployments import build_deployment_layer, deployment_key
from temporal.index import build_index


def snapshot(root, day, router=None, hf_pages=None, partial=False):
    folder = root / "data/raw" / day
    legs = {}
    if router is not None:
        stored = write_gzip(folder / "openrouter_models.json.gz",
                            json.dumps({"data": router}).encode())
        legs["openrouter_models"] = stored | {
            "status": "ok", "http_status": 200,
            "fetched_at": f"{day}T01:00:00+00:00",
            "url": "https://openrouter.ai/api/v1/models",
        }
    if hf_pages is not None:
        payload = ("\n".join(json.dumps(page) for page in hf_pages) + "\n").encode()
        stored = write_gzip(folder / "hf_inference_providers.jsonl.gz", payload)
        legs["hf_inference_providers"] = stored | {"status": "ok"}
    if partial:
        legs["hf_inference_providers"] = {"status": "partial", "http_status": 429}
    write_json(folder / "_manifest.json", {
        "schema_version": 1, "collector_version": "0.1.0", "snapshot_date": day,
        "status": "partial" if partial else "complete", "legs": legs,
    })


def page(day, models, hour=2):
    return {"page": hour - 1, "url": "https://huggingface.co/api/models?cursor=fixture",
            "fetched_at": f"{day}T{hour:02d}:00:00.123+00:00", "http_status": 200,
            "body": models}


def hf_model(repo="HF/Repo", model_id="Vendor/Exact-ID", provider="one", status="live"):
    return {"id": repo, "inferenceProviderMapping": [{
        "provider": provider, "providerId": model_id, "status": status,
        "task": "conversational", "features": {"toolCalling": False},
        "providerDetails": {"context_length": 12345, "pricing": {"input": 0.5, "output": 1.0}},
    }]}


@pytest.fixture
def history(tmp_path):
    router = {"id": "Vendor/Exact-ID", "hugging_face_id": "HF/Repo",
              "canonical_slug": "Vendor/Canonical", "context_length": 8000,
              "pricing": {"prompt": "0.0000005", "completion": "0"},
              "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
              "supported_parameters": ["tools"], "top_provider": {"max_completion_tokens": 2000}}
    snapshot(tmp_path, "2026-09-10", router=[router], hf_pages=[page("2026-09-10", [
        hf_model(), hf_model(provider="two", status="error"),
    ])])
    return tmp_path


def test_provider_namespaces_and_source_identity_evidence_are_distinct(history):
    result = build_deployment_layer(history, build_index(history))
    observations = result["deployment_observations"]
    assert len(observations) == len(result["provider_deployments"]) == 3
    assert {row["provider"] for row in observations} == {"openrouter", "one", "two"}
    assert len({row["deployment_key"] for row in observations}) == 3
    assert all(row["provider_model_id"] == "Vendor/Exact-ID" for row in observations)
    assert all(row["canonical_model_id"] is None for row in observations)
    assert all(row["canonical_model_id"] is None for row in result["provider_deployments"])
    assert all(row["source_hf_repo_id"] == "HF/Repo" for row in observations)
    assert "identity_links" not in result and "lifecycle_events" not in result
    assert deployment_key("one", "Model") != deployment_key("one", "model")
    assert deployment_key("one", "x ") != deployment_key("one", "x")


def test_source_values_share_structure_without_invented_units_or_health(history):
    rows = {row["provider"]: row for row in build_deployment_layer(
        history, build_index(history)
    )["deployment_observations"]}
    assert rows["openrouter"]["price"]["values"]["prompt"] == "0.0000005"
    assert rows["one"]["price"]["values"]["input"] == 0.5
    assert rows["openrouter"]["context"]["max_completion_tokens"] == 2000
    assert rows["one"]["context"]["context_length"] == 12345
    assert rows["one"]["capabilities"]["features"]["toolCalling"] is False
    assert rows["two"]["provider_status"] == "error"
    assert rows["two"]["presence_state"] == "PRESENT"
    assert rows["openrouter"]["provider_status"] is None
    assert all(row["price"]["unit"] is None for row in rows.values())
    assert all(row["effective_at"] is None for row in rows.values())


def test_hf_per_response_times_do_not_use_file_first_capture(history):
    day = "2026-09-11"
    snapshot(history, day, hf_pages=[page(day, [hf_model(repo="HF/A", model_id="a")]),
                                   page(day, [hf_model(repo="HF/B", model_id="b")], hour=3)])
    result = build_deployment_layer(history, build_index(history), "2026-10-03T12:00:00+00:00")
    rows = {row["provider_model_id"]: row for row in result["deployment_observations"]
            if row["observation_date"] == day}
    assert rows["a"]["observed_at"] == f"{day}T02:00:00.123+00:00"
    assert rows["b"]["observed_at"] == f"{day}T03:00:00.123+00:00"
    assert rows["b"]["recorded_at"] == "2026-10-03T12:00:00+00:00"
    assert rows["b"]["is_backfill"] is True
    assert rows["b"]["source_evidence"]["row_locator"].startswith("line[2]")


def test_observations_order_timestamp_offsets_by_actual_utc_time(history):
    pages = [page("2026-09-11", [hf_model(model_id="later")]),
             page("2026-09-11", [hf_model(model_id="earlier")], hour=3)]
    pages[0]["fetched_at"] = "2026-09-11T01:00:00+00:00"
    pages[1]["fetched_at"] = "2026-09-11T02:00:00+02:00"
    snapshot(history, "2026-09-11", hf_pages=pages)
    result = build_deployment_layer(history, build_index(history))
    ids = [row["provider_model_id"] for row in result["deployment_observations"]
           if row["observation_date"] == "2026-09-11"]
    assert ids == ["earlier", "later"]


@pytest.mark.parametrize("partial", [False, True])
def test_missing_later_evidence_stays_unknown_without_negative_facts(history, partial):
    snapshot(history, "2026-09-11", router=[], hf_pages=[] if not partial else None,
             partial=partial)
    result = build_deployment_layer(history, build_index(history))
    assert len(result["deployment_observations"]) == 3
    assert {row["presence_state"] for row in result["deployment_observations"]} == {"PRESENT"}
    assert {row["current_status"] for row in result["provider_deployments"]} == {"UNKNOWN"}
    assert result["summary"]["events_generated"] == 0
    assert all(run["can_confirm_absence"] is False for run in result["source_runs"])


def test_missing_provider_id_preserves_fact_without_fabricating_deployment(history):
    snapshot(history, "2026-09-11", hf_pages=[page("2026-09-11", [hf_model(model_id=None)])])
    result = build_deployment_layer(history, build_index(history))
    row = next(row for row in result["deployment_observations"]
               if row["provider_model_id"] is None)
    assert row["deployment_key"] is None
    assert row["deployment_id_status"] == "missing_provider_model_id"
    assert len(result["provider_deployments"]) == 3
    assert result["summary"]["unidentified_observation_count"] == 1


def test_duplicate_source_declarations_retain_separate_evidence(history):
    snapshot(history, "2026-09-11", hf_pages=[page("2026-09-11", [
        hf_model(repo="HF/A"), hf_model(repo="HF/B"),
    ])])
    result = build_deployment_layer(history, build_index(history))
    assert result["summary"]["multiple_rows_for_deployment_snapshot"] == 1
    deployment = next(row for row in result["provider_deployments"] if row["provider"] == "one")
    assert deployment["source_hf_repo_ids"] == ["HF/A", "HF/B", "HF/Repo"]
    assert len(result["deployment_observations"]) == 5


def test_replay_ids_are_stable_and_real_source_bytes_are_preserved(history):
    baseline = fingerprint(history)
    index = build_index(history)
    first = build_deployment_layer(history, index, "2026-10-03T12:00:00+00:00")
    second = build_deployment_layer(history, index, "2026-10-04T12:00:00+00:00")
    assert [row["observation_id"] for row in first["deployment_observations"]] == [
        row["observation_id"] for row in second["deployment_observations"]
    ]
    assert first["provider_deployments"] == second["provider_deployments"]
    assert verify(history, baseline)["ok"]


def test_stale_or_altered_index_cannot_materialize(history):
    index = build_index(history)
    snapshot(history, "2026-09-11", router=[])
    with pytest.raises(ValueError, match="stale or altered"):
        build_deployment_layer(history, index)
    index = build_index(history)
    index["snapshots"][0]["collected_at"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="stale or altered"):
        build_deployment_layer(history, index)


def test_unknown_capture_is_null_and_deployment_bound_keeps_day_precision(history):
    manifest = history / "data/raw/2026-09-10/_manifest.json"
    data = json.loads(manifest.read_text())
    del data["legs"]["openrouter_models"]["fetched_at"]
    write_json(manifest, data)
    result = build_deployment_layer(history, build_index(history))
    row = next(row for row in result["deployment_observations"] if row["provider"] == "openrouter")
    assert row["observed_at"] is None and row["observed_precision"] == "day"
    deployment = next(row for row in result["provider_deployments"]
                      if row["provider"] == "openrouter")
    assert deployment["first_observed_at"] is None
    assert deployment["first_observed_date"] == "2026-09-10"


def test_observation_cli_dry_run_and_guarded_output(history, capsys):
    write_new_json(history, ORIGINAL_BASELINE, fingerprint(history))
    index_path = Path("data/temporal_v2/index.json")
    write_new_json(history, index_path, build_index(history))
    args = ["--root", str(history), "observations", "--index", str(index_path)]
    assert main([*args, "--dry-run"]) == 0
    assert not (history / "data/temporal_v2/deployment_layer.json").exists()
    assert main(args) == 0
    assert main(args) == 1
    assert main([*args, "--output", "data/raw/forbidden.json"]) == 1
    assert not (history / "data/raw/forbidden.json").exists()
    capsys.readouterr()
