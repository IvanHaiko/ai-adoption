"""Replay conservative, provider-scoped identity beliefs from attested observations."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from .archive import utc_now
from .deployments import NORMALIZER_VERSION, SOURCES, _observations, stable_hash
from .index import _timestamp, build_index
from .portability import evidence_matches

IDENTITY_VERSION = "1"
PRIORITY = {"explicit_provider_mapping": 1, "exact_external_identifier": 2,
            "exact_deterministic_correspondence": 3, "curated": 4, "fuzzy": 5}
HF_REPO = re.compile(r"[^/\s]+/[^/\s]+\Z")


def validate_observations(root: Path, layer: dict, baseline: dict) -> None:
    """Reconstruct the input cutoff from raw; reject omissions and altered source facts."""
    if layer.get("schema_version") != 1 or layer.get("normalizer_version") != NORMALIZER_VERSION:
        raise ValueError("unsupported deployment observation schema")
    cutoffs = layer["summary"]["source_cutoffs"]
    if not cutoffs or set(cutoffs) - set(SOURCES):
        raise ValueError("invalid deployment source cutoffs")
    for day in cutoffs.values():
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("source cutoff must be an ISO date")
    rows = layer["deployment_observations"]
    actual = {row["observation_id"]: row for row in rows}
    if len(actual) != len(rows):
        raise ValueError("duplicate observation IDs")
    expected = {}
    manifests = {}
    for snapshot in build_index(root)["snapshots"]:
        source = snapshot["source"]
        if source not in cutoffs or snapshot["collection_day"] > cutoffs[source]:
            continue
        for row in _observations(root, snapshot, layer["recorded_at"]):
            supplied = actual.get(row["observation_id"])
            if supplied is None:
                raise ValueError("deployment observations omit source evidence at the input cutoff")
            evidence = supplied["source_evidence"]
            name, digest = evidence["manifest_path"], evidence["manifest_sha256_file"]
            if name != snapshot["manifest_path"]:
                raise ValueError("deployment evidence points to an unexpected manifest")
            pair = (name, digest)
            if pair not in manifests:
                manifests[pair] = evidence_matches(root, baseline, name, digest)
            if not manifests[pair]:
                raise ValueError("deployment manifest evidence does not match protected archive")
            row["source_evidence"]["manifest_sha256_file"] = digest
            expected[row["observation_id"]] = row
    if actual != expected:
        raise ValueError("deployment observations differ from archived source facts")


def _candidate(row: dict, method: str, repo, canonical, evidence: dict,
               evidence_at, eligible: bool, recorded_at: str) -> dict:
    if repo is not None and (not isinstance(repo, str) or not HF_REPO.fullmatch(repo)):
        eligible = False
    if canonical is not None and (not isinstance(canonical, str) or not canonical.strip()):
        raise ValueError("canonical reference must be a nonempty string or null")
    identity = [IDENTITY_VERSION, row["observation_id"], method, repo, canonical,
                evidence, evidence_at]
    return {"candidate_id": stable_hash(identity), "deployment_key": row["deployment_key"],
            "provider": row["provider"], "provider_model_id": row["provider_model_id"],
            "snapshot_id": row["snapshot_id"], "observation_id": row["observation_id"],
            "hf_repo_id": repo, "canonical_model_id": canonical, "mapping_method": method,
            "priority": PRIORITY[method], "observed_at": row["observed_at"],
            "observed_precision": row["observed_precision"], "evidence_available_at": evidence_at,
            "eligible_for_link": eligible and evidence_at is not None and method != "fuzzy",
            "review_status": evidence.get("review_status", "source_explicit"),
            "evidence": evidence, "recorded_at": recorded_at, "effective_at": None,
            "is_backfill": True}


def build_identity_history(layer: dict, ledger: dict | None = None,
                           recorded_at: str | None = None) -> dict:
    recorded_at = recorded_at or utc_now()
    _timestamp(recorded_at)
    rows = {row["observation_id"]: row for row in layer["deployment_observations"]}
    candidates = []
    batches = defaultdict(lambda: {"rows": [], "candidates": []})
    for row in rows.values():
        if not row["deployment_key"]:
            continue
        batch = batches[(row["deployment_key"], row["snapshot_id"])]
        batch["rows"].append(row)
        if row["source_hf_repo_id"]:
            candidate = _candidate(row, "explicit_provider_mapping", row["source_hf_repo_id"],
                                   None, {"source": row["source"],
                                          "source_field": "hugging_face_id"
                                          if row["source"] == "openrouter_models" else "id",
                                          "source_evidence": row["source_evidence"]},
                                   row["observed_at"], True, recorded_at)
            candidates.append(candidate)
            batch["candidates"].append(candidate)
    ledger = ledger or {"schema_version": 1, "candidates": []}
    if ledger.get("schema_version") != 1:
        raise ValueError("unsupported identity evidence ledger")
    for entry in ledger["candidates"]:
        row = rows[entry["observation_id"]]
        method = entry["mapping_method"]
        if method not in PRIORITY or method == "explicit_provider_mapping":
            raise ValueError("explicit mappings must come from the protected source")
        if not row["deployment_key"]:
            raise ValueError("review evidence needs an identified deployment")
        if not entry.get("evidence_reference"):
            raise ValueError("identity evidence needs a reference")
        status = entry.get("review_status", "pending")
        if status not in ("pending", "approved", "rejected"):
            raise ValueError("invalid review status")
        if status == "approved" and not (entry.get("reviewed_by") and entry.get("review_reason")):
            raise ValueError("approved evidence needs a reviewer and reason")
        evidence_at = entry.get("evidence_available_at")
        if evidence_at:
            if (row["observed_at"] is None
                    or _timestamp(evidence_at) < _timestamp(row["observed_at"])):
                raise ValueError("review evidence cannot precede its source observation")
            if _timestamp(evidence_at) > _timestamp(recorded_at):
                raise ValueError("cannot replay evidence from the future")
        repo, canonical = entry.get("hf_repo_id"), entry.get("canonical_model_id")
        if canonical is not None and method not in ("curated", "fuzzy"):
            raise ValueError("canonical references require curated review evidence")
        if repo is None and canonical is None:
            raise ValueError("identity candidate needs a target")
        candidate = _candidate(row, method, repo, canonical, entry, evidence_at,
                               status == "approved", recorded_at)
        candidates.append(candidate)
        # Human/external evidence becomes available at its own timestamp, never backdated.
        if evidence_at:
            batch = batches[(row["deployment_key"], "ledger:" + evidence_at)]
            batch["rows"].append({**row, "observed_at": evidence_at})
            batch["candidates"].append(candidate)
    if len({row["candidate_id"] for row in candidates}) != len(candidates):
        raise ValueError("duplicate identity candidates")
    timeline = defaultdict(lambda: defaultdict(list))
    untimed_batches = 0
    for (key, _), batch in batches.items():
        times = [row["observed_at"] for row in batch["rows"]]
        if any(time is None for time in times):
            untimed_batches += 1
            continue  # A day is not an invented midnight timestamp.
        at = max(times, key=_timestamp)
        instant = _timestamp(at).isoformat()
        if _timestamp(instant) > _timestamp(recorded_at):
            raise ValueError("cannot replay source observations from the future")
        timeline[key][instant].extend(batch["candidates"])
    decisions, links = [], []
    for key, instants in sorted(timeline.items()):
        latest = {}
        history = []
        for at, incoming in sorted(instants.items()):
            by_method = defaultdict(list)
            for candidate in incoming:
                if candidate["eligible_for_link"]:
                    by_method[candidate["mapping_method"]].append(candidate)
            latest.update(by_method)
            winning = min(latest, key=PRIORITY.get) if latest else None
            evidence = latest[winning] if winning else []
            targets = {(row["hf_repo_id"], row["canonical_model_id"]) for row in evidence}
            state = "resolved" if len(targets) == 1 else "ambiguous" if targets else "unresolved"
            repo, canonical = next(iter(targets)) if state == "resolved" else (None, None)
            canonical_status = "unassigned"
            canonical_evidence = []
            # Canonical assignment requires an explicit human review. Never create entities.
            if state == "resolved":
                canonical_evidence = [row for row in latest.get("curated", [])
                                      if row["hf_repo_id"] == repo and row["canonical_model_id"]]
                canonicals = {row["canonical_model_id"] for row in canonical_evidence}
                canonical = next(iter(canonicals)) if len(canonicals) == 1 else None
                canonical_status = "reviewed_reference" if canonical else (
                    "ambiguous" if canonicals else "unassigned")
            selected_ids = sorted({row["candidate_id"] for row in evidence + canonical_evidence})
            decision = {"decision_id": stable_hash([IDENTITY_VERSION, key, at, state, repo,
                                                    canonical, selected_ids]),
                        "deployment_key": key, "valid_from": at, "valid_to": None,
                        "state": state, "hf_repo_id": repo, "canonical_model_id": canonical,
                        "canonical_status": canonical_status, "mapping_method": winning,
                        "candidate_ids": selected_ids,
                        "considered_candidate_ids": sorted(row["candidate_id"] for row in incoming),
                        "recorded_at": recorded_at, "effective_at": None, "is_backfill": True}
            if history:
                history[-1]["valid_to"] = at
            history.append(decision)
        decisions.extend(history)
        for decision in history:
            if decision["state"] == "resolved":
                links.append({**decision, "identity_link_id": stable_hash([
                    "identity_link/1", decision["decision_id"]])})
    candidates.sort(key=lambda row: row["candidate_id"])
    return {"schema_version": 1, "identity_version": IDENTITY_VERSION,
            "recorded_at": recorded_at, "input_observations_sha256_semantic": stable_hash(layer),
            "input_ledger_sha256_semantic": stable_hash(ledger),
            "identity_candidates": candidates, "identity_decisions": decisions,
            "identity_links": links,
            "summary": {"candidate_count": len(candidates), "decision_count": len(decisions),
                        "link_count": len(links),
                        "candidates_by_method": dict(Counter(row["mapping_method"]
                                                             for row in candidates)),
                        "decision_states": dict(Counter(row["state"] for row in decisions)),
                        "canonical_assignments": sum(row["canonical_model_id"] is not None
                                                     for row in links),
                        "untimed_batches": untimed_batches, "events_generated": 0,
                        "source_cutoffs": layer["summary"]["source_cutoffs"]}}


def identity_at(history: dict, key: str, at: str, recorded_as_of: str | None = None) -> dict:
    """Query a single immutable replay; never assert pre-migration decisions existed."""
    instant = _timestamp(at)
    if recorded_as_of and _timestamp(history["recorded_at"]) > _timestamp(recorded_as_of):
        return {"deployment_key": key, "state": "not_recorded", "as_of": at}
    matching = [row for row in history["identity_decisions"] if row["deployment_key"] == key
                and _timestamp(row["valid_from"]) <= instant
                and (row["valid_to"] is None or instant < _timestamp(row["valid_to"]))]
    if len(matching) > 1:
        raise ValueError("overlapping identity decisions")
    if not matching:
        return {"deployment_key": key, "state": "unknown", "as_of": at}
    decision = matching[0]
    ids = set(decision["candidate_ids"])
    return {**decision, "as_of": at,
            "evidence": [row for row in history["identity_candidates"]
                         if row["candidate_id"] in ids]}
