"""Orchestration: decide which legs still need work, run them, write the manifest.

Re-running is the normal case, not the exception. A leg that already reached
`ok` is skipped without a single network call, which is what makes it safe to
schedule the collector twice a day against the same snapshot date.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from . import manifest as mf
from . import paths
from .fetch import HttpClient
from .sources import hf_models, openrouter_models, openrouter_rankings
from .storage import read_gzip, write_gzip


def utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Context:
    """What a leg is allowed to do: fetch, store, read back, ask the time."""

    root: Path
    snapshot_date: str
    http: HttpClient
    now: callable = utc_now

    def store(self, leg: str, data: bytes) -> dict:
        return write_gzip(paths.leg_path(self.root, self.snapshot_date, leg), data)

    def load(self, leg: str) -> bytes | None:
        path = paths.leg_path(self.root, self.snapshot_date, leg)
        return read_gzip(path) if path.exists() else None

    def leg_file(self, leg: str) -> str:
        return paths.LEG_FILES[leg]


def collect(
    root: Path,
    snapshot_date: str | None = None,
    http: HttpClient | None = None,
    now=utc_now,
) -> dict:
    snapshot_date = snapshot_date or utc_today()
    http = http or HttpClient()
    ctx = Context(root=root, snapshot_date=snapshot_date, http=http, now=now)

    path = paths.manifest_path(root, snapshot_date)
    manifest = mf.load_or_empty(path, snapshot_date)

    if manifest["status"] == mf.COMPLETE:
        # Nothing is written, not even a run entry: a no-op run must leave the
        # day byte-identical, or the twice-daily schedule would churn the repo.
        return manifest

    started_at = now()
    for leg, module in (
        ("openrouter_models", openrouter_models),
        ("openrouter_rankings", openrouter_rankings),
        ("hf_models", hf_models),
    ):
        if mf.leg_done(manifest, leg):
            continue
        previous = manifest["legs"].get(leg, {})
        if leg == "hf_models":
            manifest["legs"][leg] = module.collect(ctx, previous)
        else:
            manifest["legs"][leg] = module.collect(ctx)

    manifest["runs"].append(
        {
            "started_at": started_at,
            "finished_at": now(),
            "http_calls": http.call_count,
        }
    )
    mf.recompute_status(manifest)
    mf.save(path, manifest)
    return manifest
