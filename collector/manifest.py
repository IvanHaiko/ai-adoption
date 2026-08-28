"""The manifest is what makes a day's directory self-describing.

It records, per leg, what was requested and what came back — including what did
not come back. A leg is `ok` only when every unit of work in it reached a
terminal verdict: either stored, or a permanent failure written down. That is
what lets a re-run skip it without pretending the missing rows were zeros.
"""
from __future__ import annotations

from pathlib import Path

from . import __version__
from .storage import read_json, write_json

SCHEMA_VERSION = 1

LEGS = ("openrouter_models", "openrouter_rankings", "hf_models")

OK = "ok"
PARTIAL = "partial"
PENDING = "pending"
COMPLETE = "complete"


def empty(snapshot_date: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": snapshot_date,
        "collector_version": __version__,
        "status": PENDING,
        "runs": [],
        "legs": {leg: {"status": PENDING} for leg in LEGS},
    }


def load_or_empty(path: Path, snapshot_date: str) -> dict:
    if not path.exists():
        return empty(snapshot_date)
    manifest = read_json(path)
    # A manifest written by an older schema is not merged into silently.
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path} has schema_version {manifest.get('schema_version')!r}, "
            f"collector speaks {SCHEMA_VERSION}"
        )
    for leg in LEGS:
        manifest.setdefault("legs", {}).setdefault(leg, {"status": PENDING})
    return manifest


def leg_done(manifest: dict, leg: str) -> bool:
    return manifest["legs"].get(leg, {}).get("status") == OK


def recompute_status(manifest: dict) -> str:
    manifest["status"] = COMPLETE if all(leg_done(manifest, leg) for leg in LEGS) else PARTIAL
    return manifest["status"]


def save(path: Path, manifest: dict) -> None:
    write_json(path, manifest)
