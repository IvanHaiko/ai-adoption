"""Checkout newline tolerance is narrowly attested; source content remains strict."""
from __future__ import annotations

import json

import pytest

from collector.storage import write_gzip
from temporal.archive import fingerprint, verify
from temporal.portability import build_proof, load_proof, proof_path


@pytest.fixture
def frozen(tmp_path):
    folder = tmp_path / "data/raw/2026-09-10"
    folder.mkdir(parents=True)
    (folder / "_manifest.json").write_bytes(
        b'{\r\n  "snapshot_date": "2026-09-10",\r\n  "status": "complete"\r\n}\r\n'
    )
    write_gzip(folder / "models.json.gz", b'{}\r\n')
    baseline = fingerprint(tmp_path)
    build_proof(tmp_path, baseline)
    return tmp_path, baseline, folder


def test_linux_lf_and_windows_crlf_checkouts_match_the_same_forensic_evidence(frozen):
    root, baseline, folder = frozen
    manifest = folder / "_manifest.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original.replace(b"\r\n", b"\n"))
    result = verify(root, baseline)
    assert result["ok"] and len(result["representation_changes"]) == 1
    assert not verify(root, baseline, portable=False)["ok"]
    manifest.write_bytes(original)
    assert verify(root, baseline)["representation_changes"] == []


@pytest.mark.parametrize("change", ["content", "whitespace", "order", "gzip"])
def test_portability_does_not_hide_content_formatting_or_payload_changes(frozen, change):
    root, baseline, folder = frozen
    manifest = folder / "_manifest.json"
    body = manifest.read_bytes().replace(b"\r\n", b"\n")
    if change == "content":
        manifest.write_bytes(body.replace(b"complete", b"partial "))
    elif change == "whitespace":
        manifest.write_bytes(body.replace(b"  ", b"    "))
    elif change == "order":
        manifest.write_bytes(b'{"status":"complete","snapshot_date":"2026-09-10"}\n')
    else:
        write_gzip(folder / "models.json.gz", b'{}\n')
    assert not verify(root, baseline)["ok"]


def test_proof_cannot_be_created_after_a_content_change(frozen):
    root, baseline, folder = frozen
    (folder / "_manifest.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="exact frozen"):
        build_proof(root, baseline)


def test_tampered_portability_evidence_is_rejected(frozen):
    root, baseline, _folder = frozen
    path = proof_path(root, baseline)
    proof = json.loads(path.read_text())
    proof["manifests"][0]["representations"]["LF"]["sha256_file"] = "0" * 64
    path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="does not attest"):
        load_proof(root, baseline)
