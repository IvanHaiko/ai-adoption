"""Index existing raw payloads and capture evidence; never infer provider absence."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

from .archive import archive_files, file_hash, utc_now

INDEXER_VERSION = "2"


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"capture timestamp needs a timezone: {value}")
    return parsed.astimezone(dt.timezone.utc)


def _precision(value: str) -> str:
    fraction = value.split(".", 1)[1] if "." in value else ""
    digits = "".join(iter_digits(fraction))
    return f"fractional_seconds_{len(digits)}" if digits else "second"


def iter_digits(value):
    for character in value:
        if not character.isdigit():
            break
        yield character


def _metadata(root: Path, path: Path) -> tuple[str, dict, dict, list[dict]]:
    manifest_path = path.parent / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = path.relative_to(root).as_posix()
    if relative.startswith("data/raw/"):
        source = path.name.split(".", 1)[0]
        record = manifest.get("legs", {}).get(source, {})
        return source, manifest, record, []
    if relative.startswith("data/backfill/arxiv/"):
        return "arxiv", manifest, manifest.get("leg", {}), []
    items = [item for item in manifest.get("items", {}).values()
             if item.get("response_file") == path.name]
    hashes = {item["sha256_raw"] for item in items if item.get("sha256_raw")}
    if len(hashes) > 1:
        raise ValueError(f"conflicting manifest hashes for {relative}")
    record = {"sha256_raw": next(iter(hashes), None)}
    return "arxiv_ids", manifest, record, items


def index_snapshot(root: Path, path: Path) -> dict:
    source, manifest, record, items = _metadata(root, path)
    relative = path.relative_to(root).as_posix()
    with gzip.open(path, "rb") as handle:
        payload = handle.read()
    digest_raw = hashlib.sha256(payload).hexdigest()
    expected = record.get("sha256_raw")
    if expected is not None and digest_raw != expected:
        raise ValueError(f"manifest payload hash mismatch: {relative}")
    digest_file = file_hash(path)
    capture, response_count, observation_count = [], None, None
    basis = None
    if path.name.endswith(".jsonl.gz"):
        envelopes = [json.loads(line) for line in payload.splitlines() if line.strip()]
        capture = [page["fetched_at"] for page in envelopes if page.get("fetched_at")]
        response_count = len(envelopes)
        if source == "hf_models":
            observation_count = len(envelopes)
        elif source == "arxiv":
            observation_count = sum(len(page.get("records", [])) for page in envelopes)
        else:
            observation_count = sum(len(page["body"]) for page in envelopes)
        basis = "response_envelope.fetched_at" if capture else None
    elif source == "arxiv_ids":
        capture = [item["fetched_at"] for item in items if item.get("fetched_at")]
        response_count = 1
        basis = "enrichment_manifest.items.fetched_at" if capture else None
    else:
        if record.get("fetched_at"):
            capture = [record["fetched_at"]]
            basis = "manifest.legs.fetched_at"
        response_count = 1
        if source == "openrouter_models":
            body = json.loads(payload)
            if not isinstance(body.get("data"), list):
                raise ValueError(f"catalogue data is not an array: {relative}")
            observation_count = len(body["data"])
    if not capture and manifest.get("collected_at"):
        capture = [manifest["collected_at"]]
        basis = "manifest.collected_at"
    capture.sort(key=_timestamp)
    collected_at = capture[0] if capture else None
    collected_until = capture[-1] if capture else None
    day = manifest.get("snapshot_date")
    collection_date = _timestamp(collected_at).date().isoformat() if collected_at else day
    scope = {key: record[key] for key in (
        "scope", "filter", "sort", "target_rows", "date_from", "date_until", "metadata_prefix"
    ) if key in record}
    if source == "arxiv_ids":
        scope = {"source_interface": manifest.get("source_interface")}
    return {
        "snapshot_id": hashlib.sha256(f"{relative}\n{digest_file}".encode()).hexdigest(),
        "source": source, "source_scope": scope, "snapshot_path": relative,
        "manifest_path": (path.parent / "_manifest.json").relative_to(root).as_posix(),
        "manifest_sha256_file": file_hash(path.parent / "_manifest.json"),
        "collection_day": day, "collection_date": collection_date,
        "collected_at": collected_at, "collected_until": collected_until,
        "observed_basis": basis or ("manifest.snapshot_date" if day else "unknown"),
        "observed_precision": _precision(collected_at) if collected_at else "day" if day else None,
        "capture_timestamp_count": len(capture), "response_count": response_count,
        "model_observation_count": observation_count if source != "arxiv" else None,
        "source_record_count": observation_count,
        "content_hash": digest_file, "sha256_file": digest_file, "sha256_raw": digest_raw,
        "manifest_sha256_raw": expected,
        "integrity_status": "verified" if expected else "unattested",
        "size_bytes": path.stat().st_size, "bytes_raw": len(payload),
        "collector_version": manifest.get("collector_version"),
        "parser_version": None, "indexer_version": INDEXER_VERSION,
        "schema_version": manifest.get("schema_version"),
        "source_schema_version": record.get("schema_version"), "ingestion_run_id": None,
        "run_status": manifest.get("status"), "leg_status": record.get("status"),
        "resolution_statuses": sorted({item.get("status", "unknown") for item in items}),
        "pagination_complete": record.get("pagination_complete"),
        "absence_evidence_grade": "not_assessed", "can_confirm_absence": False,
    }


def build_index(root: Path, generated_at: str | None = None) -> dict:
    root = root.resolve()
    snapshots = [index_snapshot(root, path) for path in archive_files(root)
                 if path.name.endswith(".gz")]
    return {"schema_version": 1, "indexer_version": INDEXER_VERSION,
            "generated_at": generated_at or utc_now(), "snapshot_count": len(snapshots),
            "snapshots": snapshots}
