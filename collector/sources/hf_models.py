"""Download and like counts from the HuggingFace Hub, one call per repository.

Two things this leg has to get right.

The list of repositories comes from the *stored* OpenRouter file of the same
day, never from a fresh call — otherwise a snapshot could describe a catalogue
that was never saved.

A repository that answers 401/404 is a fact about the day, not a zero. It is
written into the manifest's `failures` and the leg still completes, because
retrying it forever would make the day permanently `partial`.
"""
from __future__ import annotations

import json

API = "https://huggingface.co/api/models/"

LEG = "hf_models"
LEG_SOURCE = "openrouter_models"

# HF answers 401 for a repository that is gated or gone; either way, asking
# again tomorrow is the right cadence, not asking again in this run.
PERMANENT_STATUS = {401, 403, 404, 410, 451}


def wanted_ids(openrouter_models_raw: bytes) -> list[str]:
    """Non-empty `hugging_face_id` values, deduplicated and ordered.

    The field is present on every model but empty for most of them, and several
    OpenRouter models point at the same repository.
    """
    catalogue = json.loads(openrouter_models_raw.decode("utf-8"))["data"]
    return sorted({m["hugging_face_id"] for m in catalogue if m.get("hugging_face_id")})


def _line(hf_id: str, fetched_at: str, body: bytes) -> bytes:
    """One JSONL record: an envelope wrapped around the response bytes.

    The body is concatenated, not re-serialised, so what lands on disk is the
    exact bytes HF sent.
    """
    envelope = json.dumps(
        {"hf_id": hf_id, "fetched_at": fetched_at, "http_status": 200},
        sort_keys=True,
    )
    return envelope[:-1].encode("utf-8") + b',"body":' + body.strip() + b"}"


def _existing(raw: bytes) -> dict[str, bytes]:
    stored = {}
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        stored[json.loads(line.decode("utf-8"))["hf_id"]] = line
    return stored


def collect(ctx, previous: dict) -> dict:
    catalogue = ctx.load(LEG_SOURCE)
    if catalogue is None:
        return {
            "status": "pending",
            "reason": "openrouter_models has not been stored for this day",
        }

    ids = wanted_ids(catalogue)
    stored = _existing(ctx.load(LEG) or b"")
    failures = {f["hf_id"]: f for f in previous.get("failures", [])}

    todo = [i for i in ids if i not in stored and i not in failures]
    transient: list[dict] = []

    for hf_id in todo:
        response = ctx.http.get(API + hf_id)
        if response.ok:
            stored[hf_id] = _line(hf_id, ctx.now(), response.body)
            continue
        failure = {
            "hf_id": hf_id,
            "http_status": response.status,
            "attempted_at": ctx.now(),
            "error": response.error,
        }
        if response.status in PERMANENT_STATUS:
            failures[hf_id] = failure
        else:
            transient.append(failure)

    payload = b"\n".join(stored[i] for i in ids if i in stored)
    if payload:
        payload += b"\n"
    record = ctx.store(LEG, payload)
    record.update(
        {
            "api": API,
            "requested": len(ids),
            "stored": sum(1 for i in ids if i in stored),
            "permanent_failures": len(failures),
            "failures": sorted(failures.values(), key=lambda f: f["hf_id"]),
            "source_file": ctx.leg_file(LEG_SOURCE),
        }
    )
    if transient:
        record["status"] = "partial"
        record["retryable"] = sorted(transient, key=lambda f: f["hf_id"])
    else:
        record["status"] = "ok"
        record["completed_at"] = ctx.now()
    return record
