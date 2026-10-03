"""Operational baseline changes retain forensic evidence and reject payload rewrites."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from temporal.__main__ import main
from temporal.archive import file_hash, fingerprint, verify, write_new_json
from temporal.baselines import (
    ACTIVATIONS,
    ENRICHMENT_MANIFEST,
    ORIGINAL_BASELINE,
    activate,
    activation_history,
    selected_baseline,
)


@pytest.fixture
def baseline_tree(tmp_path):
    folder = tmp_path / "data/enrichment/arxiv_ids"
    folder.mkdir(parents=True)
    (folder / "_manifest.json").write_text('{"schema_version":1,"items":{}}')
    (folder / "original.atom.xml.gz").write_bytes(b"protected-payload")
    original = fingerprint(tmp_path)
    write_new_json(tmp_path, ORIGINAL_BASELINE, original)
    dated = Path("data/validation/baseline_2026-10-03.json")
    write_new_json(tmp_path, dated, fingerprint(tmp_path))
    activate(tmp_path, dated, "initial active baseline; no archive changes")
    return tmp_path


def test_manifest_activation_is_explicit_and_retains_original(baseline_tree):
    root = baseline_tree
    original_bytes = (root / ORIGINAL_BASELINE).read_bytes()
    initial_activation = (root / ACTIVATIONS / "activation-000001.json").read_bytes()
    old_manifest_bytes = (root / ENRICHMENT_MANIFEST).read_bytes()
    (root / ENRICHMENT_MANIFEST).write_text('{"schema_version":1,"items":{"new":{}}}')
    original = json.loads(original_bytes)
    assert not verify(root, original)["ok"]
    candidate = Path("data/validation/baseline_2026-10-04.json")
    write_new_json(root, candidate, fingerprint(root))
    with pytest.raises(ValueError, match="unapproved protected changes"):
        activate(root, candidate, "legitimate resolver run")
    activate(root, candidate, "legitimate resolver run", accept_enrichment_manifest_change=True)
    history = activation_history(root)
    assert len(history) == 2
    assert base64.b64decode(history[0]["enrichment_manifest_evidence"]["body_base64"]) == (
        old_manifest_bytes
    )
    assert history[-1]["approved_changes"][0]["snapshot_path"] == ENRICHMENT_MANIFEST
    assert selected_baseline(root) == (root / candidate).resolve()
    assert main(["--root", str(root), "verify"]) == 0
    assert main(["--root", str(root), "verify", "--baseline", str(ORIGINAL_BASELINE)]) == 1
    assert (root / ORIGINAL_BASELINE).read_bytes() == original_bytes
    assert (root / ACTIVATIONS / "activation-000001.json").read_bytes() == initial_activation


@pytest.mark.parametrize("operation", ["rewrite", "delete"])
def test_activation_never_accepts_rewritten_or_deleted_payloads(baseline_tree, operation):
    root = baseline_tree
    payload = root / "data/enrichment/arxiv_ids/original.atom.xml.gz"
    if operation == "rewrite":
        payload.write_bytes(b"corrupted-payload")
    else:
        payload.unlink()
    candidate = Path("data/validation/baseline_2026-10-05.json")
    write_new_json(root, candidate, fingerprint(root))
    with pytest.raises(ValueError):
        activate(root, candidate, "enrichment", accept_enrichment_manifest_change=True)
    assert len(activation_history(root)) == 1


def test_activation_chain_rejects_tampered_original_or_active_files(baseline_tree):
    root = baseline_tree
    active = selected_baseline(root)
    digest = file_hash(root / ORIGINAL_BASELINE)
    active.write_bytes(active.read_bytes() + b" ")
    with pytest.raises(ValueError, match="activated baseline changed"):
        selected_baseline(root)
    assert file_hash(root / ORIGINAL_BASELINE) == digest


def test_activation_candidate_must_cover_new_files(baseline_tree):
    root = baseline_tree
    candidate = Path("data/validation/baseline_2026-10-06.json")
    write_new_json(root, candidate, fingerprint(root))
    (root / "data/enrichment/arxiv_ids/new.atom.xml.gz").write_bytes(b"new-response")
    with pytest.raises(ValueError, match="entire current archive"):
        activate(root, candidate, "new enrichment")


def test_original_file_change_breaks_selection(baseline_tree):
    root = baseline_tree
    original = root / ORIGINAL_BASELINE
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(ValueError, match="original forensic baseline changed"):
        selected_baseline(root)


def test_activation_chain_cannot_skip_a_sequence(baseline_tree):
    root = baseline_tree
    path = root / ACTIVATIONS / "activation-000001.json"
    record = json.loads(path.read_text())
    record["sequence"] = 2
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="broken baseline activation chain"):
        selected_baseline(root)


def test_unactivated_candidate_cannot_bless_source_changes(baseline_tree, capsys):
    root = baseline_tree
    (root / "data/enrichment/arxiv_ids/original.atom.xml.gz").write_bytes(b"corrupted-payload")
    candidate = Path("data/validation/unapproved.json")
    write_new_json(root, candidate, fingerprint(root))
    with pytest.raises(ValueError, match="explicitly activated baseline"):
        selected_baseline(root, candidate, require_activated=True)
    assert main(["--root", str(root), "index", "--baseline", str(candidate), "--dry-run"]) == 1
    assert "explicitly activated baseline" in capsys.readouterr().out
