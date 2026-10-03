"""Deterministic positive-evidence lifecycle events; absence is never a transition."""
from __future__ import annotations

import decimal
from bisect import bisect_right
from collections import Counter, defaultdict

from .archive import utc_now
from .deployments import stable_hash
from .identity import IDENTITY_VERSION, build_identity_history
from .index import _precision, _timestamp

ENGINE_VERSION = "1"
ENABLED = ("PROVIDER_LISTED", "PRICE_CHANGED", "CONTEXT_CHANGED", "CAPABILITY_CHANGED",
           "HF_IDENTITY_LINKED", "HF_IDENTITY_REVISED")
DEFERRED = {
    "PROVIDER_DELISTED": "requires approved source-specific absence evidence policy",
    "MODEL_REAPPEARED": "requires reliable prior confirmed delisting",
    "PROVIDER_ID_CHANGED": "requires proven continuity between distinct deployment IDs",
    "MODEL_DISCOVERED": "model entity semantics remain undecided when canonical identity is null",
}
FIELDS = {"price": "PRICE_CHANGED", "context": "CONTEXT_CHANGED",
          "capabilities": "CAPABILITY_CHANGED"}


def validate_identity(layer: dict, history: dict, ledger: dict | None = None) -> None:
    if history.get("schema_version") != 1 or history.get("identity_version") != IDENTITY_VERSION:
        raise ValueError("unsupported identity history schema")
    if history["input_observations_sha256_semantic"] != stable_hash(layer):
        raise ValueError("identity history belongs to a different deployment input")
    expected = build_identity_history(layer, ledger, history["recorded_at"])
    for field in ("input_ledger_sha256_semantic", "identity_candidates", "identity_decisions",
                  "identity_links", "summary"):
        if history[field] != expected[field]:
            raise ValueError("identity history is altered or needs its original evidence ledger")


def _leaves(value, prefix="", unordered=False):
    if value is None:
        return {}
    if isinstance(value, dict):
        result = {}
        for key, item in sorted(value.items()):
            result.update(_leaves(item, f"{prefix}.{key}" if prefix else key, unordered))
        return result
    if isinstance(value, list) and unordered:
        # Capability arrays are declarations of supported features/modalities/parameters.
        value = sorted(value, key=stable_hash)
    return {prefix: value}


def _comparison(row: dict, field: str) -> dict:
    value = row[field]
    if field == "price":
        prices = {}
        for key, item in (value["values"] or {}).items():
            if item is None:
                continue
            if isinstance(item, bool):
                prices[key] = {"type": "boolean", "value": item}
                continue
            try:
                number = decimal.Decimal(str(item))
                if not number.is_finite():
                    raise decimal.InvalidOperation
                # Decimal formatting does not imply a price change; retain raw event values.
                text = format(number, "f")
                text = text.rstrip("0").rstrip(".") if "." in text else text
                prices[key] = {"type": "decimal", "value": "0" if number == 0 else text}
            except decimal.InvalidOperation:
                prices[key] = {"type": type(item).__name__, "value": item}
        return prices
    payload = {key: item for key, item in value.items() if key != "source_field"}
    return _leaves(payload, unordered=field == "capabilities")


def build_events(layer: dict, history: dict, recorded_at: str | None = None) -> dict:
    """Inputs must be validated by the CLI against protected raw and identity replay."""
    recorded_at = recorded_at or utc_now()
    if _timestamp(recorded_at) < _timestamp(history["recorded_at"]):
        raise ValueError("events cannot be recorded before their identity input")
    observations = {row["observation_id"]: row for row in layer["deployment_observations"]}
    candidates = {row["candidate_id"]: row for row in history["identity_candidates"]}
    decisions = defaultdict(list)
    for decision in history["identity_decisions"]:
        decisions[decision["deployment_key"]].append(decision)
    for rows in decisions.values():
        rows.sort(key=lambda row: _timestamp(row["valid_from"]))
    instants = {key: [_timestamp(row["valid_from"]) for row in rows]
                for key, rows in decisions.items()}

    def belief(key, at):
        if at is None or key not in instants:
            return None
        position = bisect_right(instants[key], _timestamp(at)) - 1
        return decisions[key][position] if position >= 0 else None

    events = []

    def emit(kind, key, previous, current, old, new, field, paths=None,
             previous_decision=None, current_decision=None, at=None, day=None):
        previous_ids = sorted(row["observation_id"] for row in previous)
        current_ids = sorted(row["observation_id"] for row in current)
        reference = (current or previous)[0]
        if at is None and current and all(row["observed_at"] for row in current):
            at = max((row["observed_at"] for row in current), key=_timestamp)
        at = _timestamp(at).isoformat() if at else None
        if at and _timestamp(at) > _timestamp(recorded_at):
            raise ValueError("cannot derive events from future observations")
        decision = current_decision or belief(key, at)
        timed = [row for row in current if at and row["observed_at"]
                 and _timestamp(row["observed_at"]) == _timestamp(at)]
        precision = timed[0]["observed_precision"] if timed else "day"
        if current_decision:
            available = [candidates[key]["evidence_available_at"]
                         for key in current_decision["candidate_ids"]
                         if candidates[key]["evidence_available_at"]]
            precision = _precision(max(available, key=_timestamp)) if available else "unknown"
        previous_at = previous_decision["valid_from"] if previous_decision else max(
            (row["observed_at"] for row in previous if row["observed_at"]),
            key=_timestamp, default=None)
        evidence_rows = sorted(previous + current, key=lambda row: row["observation_id"])
        # IDs bind comparison values and ordered evidence, excluding replay timestamps.
        identity = [ENGINE_VERSION, kind, key, field, paths or [], old, new,
                    previous_ids, current_ids, at, day,
                    previous_decision["decision_id"] if previous_decision else None,
                    current_decision["decision_id"] if current_decision else None]
        events.append({"event_id": stable_hash(identity), "event_type": kind,
                       "deployment_key": key, "provider": reference["provider"],
                       "provider_model_id": reference["provider_model_id"],
                       "canonical_model_id": decision["canonical_model_id"] if decision else None,
                       "hf_repo_id": decision["hf_repo_id"] if decision else None,
                       "identity_state": decision["state"] if decision else "unknown",
                       "identity_decision_id": decision["decision_id"] if decision else None,
                       "observed_at": at, "observation_date": _timestamp(at).date().isoformat()
                       if at else day, "observed_precision": precision,
                       "observed_basis": "identity_decision.valid_from" if current_decision
                       else "positive source capture" if at else "source manifest day",
                       "effective_at": None,
                       "recorded_at": recorded_at, "is_backfill": True,
                       "field": field, "changed_paths": paths or [],
                       "previous_observed_at": previous_at,
                       "previous_value": old, "new_value": new,
                       "previous_observation_ids": previous_ids,
                       "observation_ids": current_ids,
                       "previous_snapshot_ids": sorted({row["snapshot_id"] for row in previous}),
                       "snapshot_ids": sorted({row["snapshot_id"] for row in current}),
                       "source_evidence": [{"observation_id": row["observation_id"],
                                            "observed_at": row["observed_at"],
                                            "observed_precision": row["observed_precision"],
                                            **row["source_evidence"]}
                                           for row in evidence_rows],
                       "previous_identity_decision_id": previous_decision["decision_id"]
                       if previous_decision else None,
                       "identity_candidate_ids": current_decision["candidate_ids"]
                       if current_decision else [],
                       "left_censored": kind == "PROVIDER_LISTED",
                       "algorithm_version": ENGINE_VERSION})

    groups = defaultdict(list)
    batches = defaultdict(list)
    skipped_unkeyed = 0
    for row in observations.values():
        if row["presence_state"] != "PRESENT":
            continue  # Missing/unknown/negative evidence has no event semantics in v1.
        key = row["deployment_key"]
        if key is None:
            skipped_unkeyed += 1
            continue
        groups[key].append(row)
        scope = stable_hash(row["source_scope"])
        batches[(key, row["source"], scope, row["snapshot_id"])].append(row)
    unknown_first_day = 0
    for key, rows in sorted(groups.items()):
        if any(row["observation_date"] is None for row in rows):
            unknown_first_day += 1
            continue
        first_day = min(row["observation_date"] for row in rows)
        first = [row for row in rows if row["observation_date"] == first_day]
        if all(row["observed_at"] for row in first):
            earliest = min(_timestamp(row["observed_at"]) for row in first)
            first = [row for row in first if _timestamp(row["observed_at"]) == earliest]
        emit("PROVIDER_LISTED", key, [], first, None, "PRESENT", "presence_state", day=first_day)

    timelines = defaultdict(lambda: defaultdict(list))
    unknown_time = 0
    for (key, source, scope, _), rows in batches.items():
        if any(row["observed_at"] is None for row in rows):
            unknown_time += 1
            # A dated barrier prevents a later precise row bridging unknown history.
            day = max(row["observation_date"] or "" for row in rows)
            timelines[(key, source, scope)][(day, "")].extend(rows)
        else:
            at = max(_timestamp(row["observed_at"]) for row in rows).isoformat()
            timelines[(key, source, scope)][(at[:10], at)].extend(rows)
    ambiguous_fields = Counter()
    unknown_fields = Counter()
    for (key, _, _), timeline in sorted(timelines.items()):
        anchors = {}
        untimed_days = {day for day, at in timeline if not at}
        if "" in untimed_days:
            continue
        for (day, at), rows in sorted(timeline.items()):
            rows.sort(key=lambda row: row["observation_id"])
            if day in untimed_days:
                anchors.clear()
                continue
            for field, kind in FIELDS.items():
                values = [_comparison(row, field) for row in rows]
                if len({stable_hash(value) for value in values}) != 1:
                    ambiguous_fields[field] += 1
                    anchors.pop(field, None)
                    continue
                if not values[0]:
                    unknown_fields[field] += 1
                    anchors.pop(field, None)
                    continue
                previous = anchors.get(field)
                if previous:
                    old_rows, old = previous
                    paths = sorted(path for path in old.keys() & values[0].keys()
                                   if stable_hash(old[path]) != stable_hash(values[0][path]))
                    # Units are never inferred or compared across unlike source semantics.
                    if field == "price" and any(old_rows[0][field][part] != rows[0][field][part]
                                                for part in ("currency", "unit", "source_field")):
                        paths = []
                    if paths:
                        emit(kind, key, old_rows, rows, old_rows[0][field], rows[0][field],
                             field, paths, at=at)
                anchors[field] = (rows, values[0])

    def hf_value(decision):
        repo = decision["hf_repo_id"] if decision["state"] == "resolved" else None
        targets = sorted({candidates[key]["hf_repo_id"] for key in decision["candidate_ids"]
                          if candidates[key]["hf_repo_id"]})
        return {"state": decision["state"], "hf_repo_id": repo,
                "ambiguous_hf_repo_ids": targets if decision["state"] == "ambiguous" else []}

    def source_rows(decision):
        ids = {candidates[key]["observation_id"] for key in decision["candidate_ids"]}
        return [observations[key] for key in sorted(ids)]

    for key, rows in sorted(decisions.items()):
        previous = None
        ever_linked = False
        for decision in rows:
            new = hf_value(decision)
            if new["hf_repo_id"] and not ever_linked:
                emit("HF_IDENTITY_LINKED", key, [], source_rows(decision), None, new,
                     "hf_identity", current_decision=decision, at=decision["valid_from"])
                ever_linked = True
            elif ever_linked and new != hf_value(previous):
                emit("HF_IDENTITY_REVISED", key, source_rows(previous), source_rows(decision),
                     hf_value(previous), new, "hf_identity", previous_decision=previous,
                     current_decision=decision, at=decision["valid_from"])
            previous = decision
    events.sort(key=lambda row: (row["observation_date"] or "", row["observed_at"] or "",
                                 row["deployment_key"], row["event_type"], row["event_id"]))
    if len({row["event_id"] for row in events}) != len(events):
        raise ValueError("duplicate lifecycle event IDs")
    return {"schema_version": 1, "engine_version": ENGINE_VERSION, "recorded_at": recorded_at,
            "enabled_event_types": list(ENABLED), "deferred_event_types": DEFERRED,
            "input_observations_sha256_semantic": stable_hash(layer),
            "input_identity_sha256_semantic": stable_hash(history),
            "lifecycle_events": events,
            "summary": {"event_count": len(events),
                        "events_by_type": dict(Counter(row["event_type"] for row in events)),
                        "events_by_provider": dict(Counter(row["provider"] for row in events)),
                        "source_cutoffs": layer["summary"]["source_cutoffs"],
                        "unknown_capture_batches": unknown_time,
                        "unknown_first_presence_date_deployments": unknown_first_day,
                        "ambiguous_comparison_batches_by_field": dict(ambiguous_fields),
                        "unknown_comparison_batches_by_field": dict(unknown_fields),
                        "unkeyed_positive_observations": skipped_unkeyed,
                        "absence_events_generated": 0}}
