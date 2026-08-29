"""Every path the collector writes. One place, so the layout cannot drift."""
from __future__ import annotations

import os
from pathlib import Path

RAW = "data/raw"

MANIFEST_NAME = "_manifest.json"

LEG_FILES = {
    "openrouter_models": "openrouter_models.json.gz",
    "openrouter_rankings": "openrouter_rankings.html.gz",
    "hf_models": "hf_models.jsonl.gz",
    "hf_top_models": "hf_top_models.jsonl.gz",
    "hf_new_models": "hf_new_models.jsonl.gz",
}


def repo_root() -> Path:
    """Root of the checkout. Overridable so tests never touch the real tree."""
    env = os.environ.get("COLLECTOR_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def snapshot_dir(root: Path, snapshot_date: str) -> Path:
    return root / RAW / snapshot_date


def manifest_path(root: Path, snapshot_date: str) -> Path:
    return snapshot_dir(root, snapshot_date) / MANIFEST_NAME


def leg_path(root: Path, snapshot_date: str, leg: str) -> Path:
    return snapshot_dir(root, snapshot_date) / LEG_FILES[leg]
