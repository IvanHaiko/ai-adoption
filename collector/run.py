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
from .cohort import instant, registry_path, validate_registry
from .fetch import HttpClient
from .sources import (
    arxiv,
    hf_cohort,
    hf_inference_providers,
    hf_models,
    hf_new_models,
    hf_top_models,
    openrouter_models,
    openrouter_rankings,
)
from .storage import read_gzip, read_json, write_gzip


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
    cohort_id=None,
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

    if path.exists() and snapshot_date != instant(now()).date().isoformat():
        # Historical snapshots are immutable, including incomplete days.
        return manifest

    started_at = now()
    for leg, module in (
        ("openrouter_models", openrouter_models),
        ("openrouter_rankings", openrouter_rankings),
        ("hf_models", hf_models),
        ("hf_top_models", hf_top_models),
        ("hf_new_models", hf_new_models),
        ("arxiv", arxiv),
        ("hf_inference_providers", hf_inference_providers),
    ):
        if mf.leg_done(manifest, leg):
            continue
        previous = manifest["legs"].get(leg, {})
        if leg in ("hf_models", *mf.LIST_LEGS):
            manifest["legs"][leg] = module.collect(ctx, previous)
        else:
            manifest["legs"][leg] = module.collect(ctx)

    if not mf.leg_done(manifest, "hf_cohort"):
        if not cohort_id:
            manifest["legs"]["hf_cohort"] = {
                "status": mf.NOT_APPLICABLE,
                "reason": "cohort disabled",
            }
        else:
            try:
                registry = read_json(registry_path(root, cohort_id))
                validate_registry(registry)
                if registry["cohort_id"] != cohort_id:
                    raise ValueError("registry cohort_id differs from requested cohort_id")
            except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError) as exc:
                manifest["legs"]["hf_cohort"] = {
                    "status": mf.PARTIAL,
                    "failure_kind": "missing_registry"
                    if isinstance(exc, FileNotFoundError)
                    else "invalid_registry",
                    "reason": (
                        f"Initialize and commit cohort {cohort_id!r} "
                        f"offline before collection: {exc}"
                    ),
                }
            else:
                manifest["legs"]["hf_cohort"] = hf_cohort.collect(
                    ctx, manifest["legs"].get("hf_cohort", {}), registry
                )

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
