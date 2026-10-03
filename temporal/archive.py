"""Freeze and verify stored archive bytes without changing the archive."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

PROTECTED_ROOTS = ("data/raw", "data/backfill/arxiv", "data/enrichment/arxiv_ids")
OUTPUT_ROOTS = ("data/validation", "data/temporal_v2")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_files(root: Path) -> list[Path]:
    root = root.resolve()
    result = []
    for folder in PROTECTED_ROOTS:
        base = root / folder
        if base.is_symlink() or base.resolve() != base:
            raise ValueError(f"protected root is redirected: {base}")
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or path.resolve() != path:
                raise ValueError(f"archive path is redirected: {path}")
            if path.is_file():
                result.append(path)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def output_path(root: Path, path: Path) -> Path:
    root = root.resolve()
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root / folder) for folder in OUTPUT_ROOTS):
        raise ValueError("output must be inside data/validation or data/temporal_v2")
    return resolved


def write_new_json(root: Path, path: Path, value: dict) -> Path:
    """Exclusive creation: baseline and derived runs cannot overwrite any file."""
    path = output_path(root, path)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return path


def _capture_times(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("fetched_at", "collected_at") and isinstance(item, str):
                try:
                    parsed = dt.datetime.fromisoformat(item.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        yield parsed.astimezone(dt.timezone.utc).isoformat()
                except ValueError:
                    pass
            else:
                yield from _capture_times(item)
    elif isinstance(value, list):
        for item in value:
            yield from _capture_times(item)


def fingerprint(root: Path, generated_at: str | None = None) -> dict:
    root = root.resolve()
    entries, times = [], []
    for path in archive_files(root):
        entries.append({"snapshot_path": path.relative_to(root).as_posix(),
                        "size_bytes": path.stat().st_size, "sha256_file": file_hash(path)})
        if path.name == "_manifest.json":
            times.extend(_capture_times(json.loads(path.read_text(encoding="utf-8"))))
    return {
        "schema_version": 1, "generated_at": generated_at or utc_now(),
        "protected_roots": list(PROTECTED_ROOTS), "file_count": len(entries),
        "total_bytes": sum(entry["size_bytes"] for entry in entries),
        "min_timestamp": min(times) if times else None,
        "max_timestamp": max(times) if times else None,
        "timestamp_basis": (
            "manifest fetched_at/collected_at; capture bounds, not source-effective dates"
        ),
        "files": entries,
    }


def verify(root: Path, baseline: dict, portable: bool = True) -> dict:
    root = root.resolve()
    if baseline.get("schema_version") != 1:
        raise ValueError("unsupported baseline version")
    if baseline.get("protected_roots") != list(PROTECTED_ROOTS):
        raise ValueError("baseline protected roots differ from the approved archive set")
    entries = baseline["files"]
    paths = [entry["snapshot_path"] for entry in entries]
    if len(paths) != len(set(paths)) or baseline["file_count"] != len(paths):
        raise ValueError("baseline has duplicate paths or an inconsistent file count")
    if baseline["total_bytes"] != sum(entry["size_bytes"] for entry in entries):
        raise ValueError("baseline total_bytes is inconsistent")
    current = {path.relative_to(root).as_posix(): path for path in archive_files(root)}
    from .portability import load_proof

    proof = load_proof(root, baseline) if portable else {}
    missing, changed, representation_changes = [], [], []
    for entry in entries:
        name = entry["snapshot_path"]
        # Never follow a malicious baseline path outside the protected set.
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not any(
            relative.is_relative_to(Path(folder)) for folder in PROTECTED_ROOTS
        ):
            raise ValueError(f"invalid protected baseline path: {name}")
        path = current.get(name)
        if path is None:
            missing.append(name)
        else:
            size, actual_hash = path.stat().st_size, file_hash(path)
            if size == entry["size_bytes"] and actual_hash == entry["sha256_file"]:
                continue
            allowed = proof.get(name, {}).get("representations", {})
            representation = next((label for label, value in allowed.items()
                                   if size == value["size_bytes"]
                                   and actual_hash == value["sha256_file"]), None)
            if representation:
                representation_changes.append({"snapshot_path": name,
                                               "representation": representation,
                                               "sha256_file": actual_hash})
            else:
                changed.append(name)
    added = sorted(current.keys() - set(paths))
    return {
        "ok": not missing and not changed, "baseline_file_count": len(entries),
        "baseline_total_bytes": baseline["total_bytes"], "current_file_count": len(current),
        "current_total_bytes": sum(path.stat().st_size for path in current.values()),
        "missing": missing, "changed": changed, "added": added,
        "representation_changes": representation_changes,
    }
