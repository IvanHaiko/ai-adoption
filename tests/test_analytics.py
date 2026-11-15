import gzip
import hashlib
import json

import pytest

from analytics.build import project


def _snapshot(root, day, legs):
    folder = root / "data/raw" / day
    folder.mkdir(parents=True)
    manifest = {"status": "complete", "legs": {}}
    for name, (metadata, envelopes) in legs.items():
        payload = b"\n".join(json.dumps(r).encode() for r in envelopes)
        filename = name + ".jsonl.gz"
        (folder / filename).write_bytes(gzip.compress(payload))
        manifest["legs"][name] = dict(metadata, file=filename,
                                      sha256_raw=hashlib.sha256(payload).hexdigest())
    (folder / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_projection_retains_unknown_and_no_rename_inference(tmp_path):
    registry = tmp_path / "data/cohorts"
    registry.mkdir(parents=True)
    (registry / "fixed.json").write_text(json.dumps({"members": [
        {"hf_repo_id": "org/a", "selection_group": "recent"},
        {"hf_repo_id": "org/b", "selection_group": "recent"}]}))
    _snapshot(tmp_path, "2026-01-01", {
        "hf_cohort": ({"status": "ok", "cohort_id": "fixed", "failures": [
            {"hf_id": "org/b", "reason": "identity_mismatch", "response_id": "new/b"}]},
            [{"hf_id": "org/a", "body": {"id": "org/a"}}]),
        "hf_inference_providers": ({"status": "ok"}, [{"body": [
            {"id": "org/a", "inferenceProviderMapping": [
                {"provider": "host", "status": "live"},
                {"provider": "host", "status": "live"},
                {"provider": "broken", "status": "error"}]},
            {"id": "org/b", "inferenceProviderMapping": []}]}]),
    })
    _snapshot(tmp_path, "2026-01-03", {})
    result = project(tmp_path)
    assert result["cohort_coverage"][0]["coverage"] == .5
    assert result["cohort_coverage"][1]["source_available"] is False
    assert json.loads(result["cohort_coverage"][1]["missing_reasons"]) == {
        "source_unavailable": 2}
    assert result["identity_review_queue"][0]["decision"] == "pending_review"
    assert result["hosting_day"][0]["live_provider_count"] == 1
    assert result["hosting_day"][1]["mapping_state"] == "unknown"
    assert len(result["hosting_day"]) == 2  # No invented later zero/absence rows.
    missing = [r for r in result["source_coverage"]
               if r["source"] == "hf_cohort" and r["snapshot_date"] == "2026-01-02"]
    assert missing[0]["status"] == "missing"
    assert result == project(tmp_path)


def test_source_payload_tampering_rejected(tmp_path):
    _snapshot(tmp_path, "2026-01-01", {"hf_inference_providers": (
        {"status": "ok"}, [{"body": []}])})
    path = tmp_path / "data/raw/2026-01-01/hf_inference_providers.jsonl.gz"
    path.write_bytes(gzip.compress(b'{"body": [{"id":"invented"}]}'))
    with pytest.raises(ValueError, match="payload hash mismatch"):
        project(tmp_path)
