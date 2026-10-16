"""Daily exact-ID follow-up of a fixed, explicitly enrolled cohort."""

from __future__ import annotations

import base64
import json
import time
from collections import Counter
from contextlib import suppress

from ..cohort import DISCOVERY, counts_valid, envelope_bodies, envelopes, instant, valid_id
from .hf_models import API, PERMANENT_STATUS

LEG = "hf_cohort"
POLL_BUDGET_SECONDS = 1800


def response_reason(body, rid):
    if not isinstance(body, dict):
        return "invalid_response"
    identities = [body[k] for k in ("id", "modelId") if body.get(k) is not None]
    if identities and any(value != rid for value in identities):
        return "identity_mismatch"
    if not identities or not counts_valid(body):
        return "invalid_response"
    return None


def same_day(value, day):
    try:
        return instant(value).date().isoformat() == day
    except (ValueError, TypeError, AttributeError):
        return False


def terminal_failure(failure, day):
    """An unavailable or consistently changed identity is a verdict for this day."""
    response_id = failure.get("response_id")
    return failure.get("http_status") in PERMANENT_STATUS or (
        failure.get("http_status") == 200
        and failure.get("reason") == "identity_mismatch"
        and valid_id(response_id)
        and response_id != failure.get("hf_id")
        and same_day(failure.get("measured_at"), day)
        and all(value == response_id for value in failure.get("response_ids", {}).values())
    )


def line(metadata, body):
    if b"\r" in body or b"\n" in body:
        metadata = dict(metadata, body_raw_base64=base64.b64encode(body).decode("ascii"))
        body = json.dumps(json.loads(body), separators=(",", ":")).encode()
    return json.dumps(metadata, sort_keys=True).encode()[:-1] + b',"body":' + body + b"}"


def collect(ctx, previous, registry):
    started = time.monotonic()
    day = ctx.snapshot_date
    if not same_day(ctx.now(), day):
        return {
            "status": "not_applicable",
            "reason": "cohort polling requires the current UTC date",
            "requested": 0,
            "stored": 0,
            "coverage": dict(
                cohort_size=len(registry["members"]),
                attempted=0,
                successful=0,
                failed_by_reason={},
                deferred=0,
                deferred_by_reason={},
                coverage=None,
                already_observed_through_discovery=0,
                observed_only_through_cohort=0,
            ),
        }
    members = registry["members"]
    eligible = sorted(
        {
            m["hf_repo_id"]
            for m in members
            if m.get("active", True)
            and day >= instant(m.get("enrolled_at", registry["selected_at"])).date().isoformat()
            and day >= instant(registry["selected_at"]).date().isoformat()
        }
    )
    stored = {}
    for raw_line in (ctx.load(LEG) or b"").splitlines():
        try:
            row = json.loads(raw_line)
            rid = row["hf_id"]
            if (
                rid in eligible
                and row.get("cohort_id") == registry["cohort_id"]
                and row.get("http_status") == 200
                and same_day(row.get("fetched_at"), day)
                and response_reason(row.get("body"), rid) is None
            ):
                stored.setdefault(rid, raw_line)
        except (ValueError, KeyError, TypeError):
            continue
    failures = {
        f["hf_id"]: f
        for f in previous.get("failures", [])
        if f.get("hf_id") in eligible
        and same_day(f.get("attempted_at"), day)
        and terminal_failure(f, day)
    }
    discovery, sources = {}, {}
    for leg in DISCOVERY:
        for row in envelopes(ctx.load(leg) or b""):
            if row.get("http_status") != 200 or not same_day(row.get("fetched_at"), day):
                continue
            for body in envelope_bodies(row):
                rid = body.get("id") or body.get("modelId")
                if rid not in eligible or response_reason(body, rid):
                    continue
                if leg not in sources.setdefault(rid, []):
                    sources[rid].append(leg)
                discovery.setdefault(rid, (body, row, leg))
    for rid, (body, row, leg) in discovery.items():
        if rid in stored or rid in failures:
            continue
        metadata = dict(
            hf_id=rid,
            cohort_id=registry["cohort_id"],
            fetched_at=row["fetched_at"],
            http_status=200,
            source_leg=leg,
            discovery_sources=sources[rid],
        )
        url = row.get("source_url") or row.get("url")
        if url:
            metadata["source_url"] = url
        stored[rid] = line(metadata, json.dumps(body, ensure_ascii=False).encode())
    retryable = []
    deferred = []
    history = list(previous.get("attempt_history", []))
    history.extend(f for f in previous.get("retryable", []) if same_day(f.get("attempted_at"), day))
    old_interval = getattr(ctx.http, "min_interval", None)
    try:
        if old_interval is not None:
            ctx.http.min_interval = max(old_interval, 0.75)
        for rid in eligible:
            if rid in stored or rid in failures:
                continue
            attempted_at = ctx.now()
            defer_reason = (
                "snapshot_date_ended"
                if not same_day(attempted_at, day)
                else "budget_exhausted"
                if time.monotonic() - started >= POLL_BUDGET_SECONDS
                else None
            )
            if defer_reason:
                attempted_ids = {f["hf_id"] for f in retryable}
                deferred.extend(
                    dict(hf_id=missing, reason=defer_reason)
                    for missing in eligible
                    if missing not in stored
                    and missing not in failures
                    and missing not in attempted_ids
                )
                break
            response = ctx.http.get(API + rid)
            measured_at = ctx.now()
            reason = None
            body = None
            if response.status == 200:
                with suppress(ValueError, TypeError, UnicodeDecodeError):
                    body = json.loads(response.body)
                reason = response_reason(body, rid)
                if reason is None and not same_day(measured_at, day):
                    reason = "snapshot_date_ended"
                if reason is None:
                    stored[rid] = line(
                        dict(
                            hf_id=rid,
                            cohort_id=registry["cohort_id"],
                            fetched_at=measured_at,
                            http_status=200,
                            source_leg=LEG,
                            source_url=response.url,
                            discovery_sources=sources.get(rid, []),
                        ),
                        response.body,
                    )
                    continue
            else:
                reason = (
                    "rate_limited"
                    if response.status == 429
                    else "timeout_or_connection"
                    if response.status == 0
                    else "server_error"
                    if 500 <= response.status < 600
                    else "http_error"
                )
            failure = dict(
                hf_id=rid,
                attempted_at=attempted_at,
                measured_at=measured_at,
                http_status=response.status,
                reason=reason,
            )
            if getattr(response, "error", None):
                failure["error"] = response.error
            if getattr(response, "attempts", None) is not None:
                failure["attempts"] = response.attempts
            if reason == "snapshot_date_ended":
                failure["error"] = (
                    "Response arrived after UTC snapshot date ended; "
                    "retry on the current snapshot date"
                )
            if reason == "identity_mismatch":
                failure["response_id"] = body.get("id") or body.get("modelId")
                failure["response_ids"] = {
                    key: body[key] for key in ("id", "modelId") if body.get(key) is not None
                }
            if terminal_failure(failure, day):
                failures[rid] = failure
            else:
                retryable.append(failure)
    finally:
        if old_interval is not None:
            ctx.http.min_interval = old_interval
    record = ctx.store(
        LEG,
        b"\n".join(stored[rid] for rid in eligible if rid in stored) + (b"\n" if stored else b""),
    )
    all_failures = list(failures.values()) + retryable
    reused = sum(json.loads(raw).get("source_leg") in DISCOVERY for raw in stored.values())
    record.update(
        status="partial" if retryable or deferred else "ok",
        cohort_id=registry["cohort_id"],
        requested=len(eligible),
        stored=len(stored),
        failures=list(failures.values()),
        retryable=retryable,
        deferred=deferred,
        attempt_history=history,
        coverage=dict(
            cohort_size=len(members),
            attempted=len(stored) + len(all_failures),
            successful=len(stored),
            failed_by_reason=dict(Counter(f["reason"] for f in all_failures)),
            deferred=len(deferred),
            deferred_by_reason=dict(Counter(f["reason"] for f in deferred)),
            coverage=len(stored) / len(eligible) if eligible else None,
            already_observed_through_discovery=reused,
            observed_only_through_cohort=len(stored) - reused,
        ),
    )
    return record
