"""Append-only activation journal for dated operational baselines."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from .archive import file_hash, output_path, utc_now, verify, write_new_json

ORIGINAL_BASELINE = Path("data/validation/historical_archive_baseline.json")
ACTIVATIONS = Path("data/validation/baseline_activations")
ENRICHMENT_MANIFEST = "data/enrichment/arxiv_ids/_manifest.json"


def activation_history(root: Path) -> list[dict]:
    root = root.resolve()
    folder = output_path(root, ACTIVATIONS)
    result = []
    previous_hash = None
    previous_baseline_path = ORIGINAL_BASELINE.as_posix()
    for sequence, path in enumerate(sorted(folder.glob("activation-*.json")), 1):
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("schema_version") != 1 or record.get("sequence") != sequence
                or path.name != f"activation-{sequence:06d}.json"
                or record.get("previous_activation_sha256_file") != previous_hash):
            raise ValueError("broken baseline activation chain")
        if record.get("original_baseline_path") != ORIGINAL_BASELINE.as_posix():
            raise ValueError("activation points to a different original baseline")
        if record.get("previous_baseline_path") != previous_baseline_path:
            raise ValueError("broken baseline ancestry")
        if file_hash(root / ORIGINAL_BASELINE) != record["original_baseline_sha256_file"]:
            raise ValueError("original forensic baseline changed")
        baseline_path = output_path(root, Path(record["baseline_path"]))
        if file_hash(baseline_path) != record["baseline_sha256_file"]:
            raise ValueError(f"activated baseline changed: {baseline_path}")
        evidence = record.get("enrichment_manifest_evidence")
        if evidence is not None:
            body = base64.b64decode(evidence["body_base64"], validate=True)
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            entries = {entry["snapshot_path"]: entry for entry in baseline["files"]}
            if (evidence["snapshot_path"] != ENRICHMENT_MANIFEST
                    or hashlib.sha256(body).hexdigest() != evidence["sha256_file"]
                    or entries[ENRICHMENT_MANIFEST]["sha256_file"] != evidence["sha256_file"]):
                raise ValueError("enrichment manifest evidence changed")
        previous_hash = file_hash(path)
        previous_baseline_path = record["baseline_path"]
        result.append(record)
    return result


def selected_baseline(root: Path, explicit: Path | None = None,
                      require_activated: bool = False) -> Path:
    history = activation_history(root)
    if explicit is not None:
        selected = output_path(root, explicit)
        known = {output_path(root, ORIGINAL_BASELINE),
                 *(output_path(root, Path(record["baseline_path"])) for record in history)}
        if require_activated and selected not in known:
            raise ValueError(
                "publication requires the original or an explicitly activated baseline"
            )
        return selected
    selected = Path(history[-1]["baseline_path"]) if history else ORIGINAL_BASELINE
    return output_path(root, selected)


def activate(root: Path, candidate: Path, reason: str,
             accept_enrichment_manifest_change: bool = False) -> dict:
    root = root.resolve()
    if not reason.strip():
        raise ValueError("baseline activation needs a nonempty reason")
    history = activation_history(root)
    candidate_path = output_path(root, candidate)
    if candidate_path == root / ORIGINAL_BASELINE:
        raise ValueError("active baseline must be a separate dated file")
    if any(record["baseline_path"] == candidate_path.relative_to(root).as_posix()
           for record in history):
        raise ValueError("baseline already activated")
    current = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_result = verify(root, current)
    if not candidate_result["ok"] or candidate_result["added"]:
        raise ValueError("candidate baseline must match the entire current archive")
    previous_path = selected_baseline(root)
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    changes = verify(root, previous)
    if changes["missing"]:
        raise ValueError("cannot activate after deletion of a protected file")
    allowed = {ENRICHMENT_MANIFEST} if accept_enrichment_manifest_change else set()
    if set(changes["changed"]) - allowed:
        raise ValueError("unapproved protected changes; only explicit enrichment manifest "
                         "acceptance is supported")
    previous_entries = {row["snapshot_path"]: row for row in previous["files"]}
    current_entries = {row["snapshot_path"]: row for row in current["files"]}
    approved_changes = [
        {"snapshot_path": name, "before_sha256_file": previous_entries[name]["sha256_file"],
         "after_sha256_file": current_entries[name]["sha256_file"]}
        for name in changes["changed"]
    ]
    manifest = root / ENRICHMENT_MANIFEST
    manifest_bytes = manifest.read_bytes() if manifest.exists() else None
    record = {
        "schema_version": 1, "sequence": len(history) + 1, "activated_at": utc_now(),
        "reason": reason, "baseline_path": candidate_path.relative_to(root).as_posix(),
        "baseline_sha256_file": file_hash(candidate_path),
        "original_baseline_path": ORIGINAL_BASELINE.as_posix(),
        "original_baseline_sha256_file": file_hash(root / ORIGINAL_BASELINE),
        "previous_baseline_path": previous_path.relative_to(root).as_posix(),
        "previous_activation_sha256_file": file_hash(
            root / ACTIVATIONS / f"activation-{len(history):06d}.json"
        ) if history else None,
        "approved_changes": approved_changes, "added": changes["added"],
        "checkout_representation_changes": changes["representation_changes"],
        "enrichment_manifest_evidence": {
            "snapshot_path": ENRICHMENT_MANIFEST,
            "sha256_file": hashlib.sha256(manifest_bytes).hexdigest(),
            "body_base64": base64.b64encode(manifest_bytes).decode("ascii"),
        } if manifest_bytes is not None else None,
    }
    # No archive changes may race the approval record.
    final_check = verify(root, current)
    if final_check != candidate_result:
        raise ValueError("archive changed during activation")
    write_new_json(root, ACTIVATIONS / f"activation-{len(history) + 1:06d}.json", record)
    return record
