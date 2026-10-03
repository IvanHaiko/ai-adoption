"""Attest only LF/CRLF checkout representations of frozen manifest bytes."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from .archive import fingerprint, output_path, utc_now, verify, write_new_json


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def baseline_id(baseline: dict) -> str:
    return digest(json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode())


def proof_path(root: Path, baseline: dict) -> Path:
    return output_path(root, Path("data/validation/archive_portability")
                       / (baseline_id(baseline) + ".json"))


def representations(body: bytes) -> dict[str, dict]:
    # No JSON reserialization, whitespace stripping, or lone-CR normalization.
    lf = body.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return {label: {"sha256_file": digest(value), "size_bytes": len(value)}
            for label, value in (("LF", lf), ("CRLF", crlf))}


def load_proof(root: Path, baseline: dict) -> dict[str, dict]:
    path = proof_path(root, baseline)
    if not path.exists():
        return {}
    proof = json.loads(path.read_text(encoding="utf-8"))
    if proof.get("schema_version") != 1 or proof.get("baseline_id") != baseline_id(baseline):
        raise ValueError("invalid archive portability proof")
    expected = {entry["snapshot_path"]: entry for entry in baseline["files"]}
    result = {}
    for entry in proof["manifests"]:
        name = entry["snapshot_path"]
        if Path(name).name != "_manifest.json" or name not in expected or name in result:
            raise ValueError("portability proof contains an unexpected or duplicate path")
        body = base64.b64decode(entry["body_base64"], validate=True)
        original = expected[name]
        if (digest(body) != original["sha256_file"] or len(body) != original["size_bytes"]
                or representations(body) != entry["representations"]):
            raise ValueError(f"portability evidence does not attest the frozen bytes: {name}")
        result[name] = entry
    return result


def build_proof(root: Path, baseline: dict) -> dict:
    root = root.resolve()
    before = verify(root, baseline, portable=False)
    if not before["ok"]:
        raise ValueError("portability must be attested from exact frozen archive bytes")
    archive_before = fingerprint(root)["files"]
    manifests = []
    for entry in baseline["files"]:
        if Path(entry["snapshot_path"]).name != "_manifest.json":
            continue
        body = (root / entry["snapshot_path"]).read_bytes()
        if digest(body) != entry["sha256_file"]:
            raise ValueError("archive changed while collecting portability evidence")
        manifests.append({"snapshot_path": entry["snapshot_path"],
                          "body_base64": base64.b64encode(body).decode("ascii"),
                          "representations": representations(body)})
    proof = {"schema_version": 1, "baseline_id": baseline_id(baseline),
             "generated_at": utc_now(), "policy": "exact frozen manifest or attested LF/CRLF only",
             "manifests": manifests}
    if (verify(root, baseline, portable=False) != before
            or fingerprint(root)["files"] != archive_before):
        raise ValueError("archive changed while attesting portability")
    write_new_json(root, proof_path(root, baseline), proof)
    return proof


def evidence_matches(root: Path, baseline: dict, name: str, expected_hash: str) -> bool:
    """Match old source evidence without weakening compressed-payload integrity."""
    current = (root / name).read_bytes()
    if digest(current) == expected_hash:
        return True
    entry = load_proof(root, baseline).get(name)
    if entry is None:
        return False
    original = base64.b64decode(entry["body_base64"], validate=True)
    known = {digest(original), *(value["sha256_file"]
                                 for value in entry["representations"].values())}
    return expected_hash in known and digest(current) in known
