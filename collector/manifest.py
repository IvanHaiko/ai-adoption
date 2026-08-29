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

LEGS = (
    "openrouter_models",
    "openrouter_rankings",
    "hf_models",
    "hf_top_models",
    "hf_new_models",
)

# Legs that walk a paginated list and report a `rows` count.
LIST_LEGS = ("hf_top_models", "hf_new_models")

OK = "ok"
PARTIAL = "partial"
PENDING = "pending"
COMPLETE = "complete"

# A leg that did not exist when this day was collected. The day is finished and
# can never contain it: collecting it now would stamp today's data with an old
# date. Counts as done, and is never retried.
NOT_APPLICABLE = "not_applicable"

DONE = (OK, NOT_APPLICABLE)


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
    # A leg added to the collector after this day was already finished cannot be
    # backfilled - the ranking it would fetch is today's, not that day's. Mark
    # it so, rather than reopening a closed day and filling it with a lie.
    default = (
        {"status": NOT_APPLICABLE, "reason": "leg added after this day was collected"}
        if manifest.get("status") == COMPLETE
        else {"status": PENDING}
    )
    for leg in LEGS:
        manifest.setdefault("legs", {}).setdefault(leg, dict(default))
    return manifest


def leg_done(manifest: dict, leg: str) -> bool:
    """True when the leg needs no further work, whether or not it has data."""
    return manifest["legs"].get(leg, {}).get("status") in DONE


def recompute_status(manifest: dict) -> str:
    manifest["status"] = COMPLETE if all(leg_done(manifest, leg) for leg in LEGS) else PARTIAL
    return manifest["status"]


def save(path: Path, manifest: dict) -> None:
    write_json(path, manifest)
