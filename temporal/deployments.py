"""Temporal source facts and provider-scoped deployments, without identity inference."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from collector.storage import read_gzip

from .archive import archive_files, utc_now
from .index import INDEXER_VERSION, _precision, _timestamp, build_index

SOURCES = ("openrouter_models", "hf_inference_providers")
NORMALIZER_VERSION = "1"


def stable_hash(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deployment_key(provider: str, model_id: str) -> str:
    return stable_hash(["provider_deployment/1", provider, model_id])


def _optional_object(value, where: str):
    if value is not None and not isinstance(value, dict):
        raise ValueError(f"expected an object at {where}")
    return value


def _fact(snapshot: dict, provider: str, model_id, locator: str, raw: dict,
          observed_at, source_url, recorded_at: str, hf_id=None) -> dict:
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError(f"missing provider at {snapshot['snapshot_path']}:{locator}")
    if model_id is not None and not isinstance(model_id, str):
        raise ValueError(f"provider model ID must be a source string at {locator}")
    identified = isinstance(model_id, str) and bool(model_id.strip())
    key = deployment_key(provider, model_id) if identified else None
    source = snapshot["source"]
    if observed_at is not None:
        observation_date = _timestamp(observed_at).date().isoformat()
        precision = _precision(observed_at)
    else:
        observation_date = snapshot["collection_day"]
        precision = "day" if observation_date else None
    if source == "openrouter_models":
        details = _optional_object(raw.get("top_provider"), locator) or {}
        pricing = _optional_object(raw.get("pricing"), locator)
        context_length = raw.get("context_length")
        price_field, context_field = "pricing", "context_length"
        capabilities = {"architecture": raw.get("architecture"),
                        "supported_parameters": raw.get("supported_parameters"),
                        "features": None, "task": None}
    else:
        details = _optional_object(raw.get("providerDetails"), locator) or {}
        pricing = _optional_object(details.get("pricing"), locator)
        context_length = details.get("context_length")
        price_field = "inferenceProviderMapping.providerDetails.pricing"
        context_field = "inferenceProviderMapping.providerDetails.context_length"
        capabilities = {"architecture": None, "supported_parameters": None,
                        "features": raw.get("features"), "task": raw.get("task")}
    row_hash = stable_hash(raw)
    return {
        "observation_id": stable_hash(["deployment_observation/1", snapshot["snapshot_id"],
                                       provider, model_id, locator, row_hash]),
        "deployment_key": key, "snapshot_id": snapshot["snapshot_id"],
        "provider": provider, "provider_model_id": model_id, "canonical_model_id": None,
        "deployment_id_status": "identified" if identified else "missing_provider_model_id",
        "observed_at": observed_at, "observation_date": observation_date,
        "observed_precision": precision,
        "observed_basis": "response_envelope.fetched_at" if source != "openrouter_models"
        and observed_at else snapshot["observed_basis"] if source == "openrouter_models"
        else "manifest.snapshot_date" if observation_date else "unknown",
        "effective_at": None, "recorded_at": recorded_at, "is_backfill": True,
        "presence_state": "PRESENT", "provider_status": raw.get("status")
        if source != "openrouter_models" else None,
        "price": {"values": pricing, "source_field": price_field,
                  "currency": None, "unit": None},
        "context": {"context_length": context_length,
                    "max_completion_tokens": details.get("max_completion_tokens"),
                    "source_field": context_field},
        "capabilities": capabilities,
        "source": source, "source_scope": snapshot["source_scope"],
        "source_hf_repo_id": hf_id,
        "source_publisher": model_id.split("/", 1)[0]
        if source == "openrouter_models" and identified else None,
        "source_canonical_slug": raw.get("canonical_slug")
        if source == "openrouter_models" else None,
        "source_evidence": {
            "snapshot_path": snapshot["snapshot_path"],
            "sha256_file": snapshot["sha256_file"], "sha256_raw": snapshot["sha256_raw"],
            "manifest_path": snapshot["manifest_path"],
            "manifest_sha256_file": snapshot["manifest_sha256_file"],
            "row_locator": locator, "source_row_sha256": row_hash, "source_url": source_url,
            "collection_day": snapshot["collection_day"],
            "run_status": snapshot["run_status"], "leg_status": snapshot["leg_status"],
            "can_confirm_absence": False,
        },
    }


def _observations(root: Path, snapshot: dict, recorded_at: str) -> list[dict]:
    payload = read_gzip(root / snapshot["snapshot_path"])
    if snapshot["source"] == "openrouter_models":
        models = json.loads(payload)["data"]
        manifest = json.loads((root / snapshot["manifest_path"]).read_text(encoding="utf-8"))
        leg = manifest.get("legs", {}).get("openrouter_models", {})
        result = []
        for number, model in enumerate(models):
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                raise ValueError(f"OpenRouter row has no string ID at data[{number}]")
            result.append(_fact(snapshot, "openrouter", model["id"], f"data[{number}]", model,
                                leg.get("fetched_at"), leg.get("url"), recorded_at,
                                model.get("hugging_face_id")))
        return result
    result = []
    for line_number, line in enumerate(payload.splitlines(), 1):
        if not line.strip():
            continue
        envelope = json.loads(line)
        if not isinstance(envelope.get("body"), list):
            raise ValueError(f"HF page body is not an array at line {line_number}")
        for number, model in enumerate(envelope["body"]):
            locator = f"line[{line_number}].body[{number}]"
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                raise ValueError(f"HF model has no string ID at {locator}")
            mappings = model.get("inferenceProviderMapping")
            if not isinstance(mappings, list):
                raise ValueError(f"HF mapping array is missing at {locator}")
            for mapping_number, mapping in enumerate(mappings):
                if not isinstance(mapping, dict):
                    raise ValueError(f"HF mapping is not an object at {locator}")
                result.append(_fact(
                    snapshot, mapping.get("provider"), mapping.get("providerId"),
                    f"{locator}.inferenceProviderMapping[{mapping_number}]", mapping,
                    envelope.get("fetched_at"), envelope.get("url"), recorded_at, model["id"],
                ))
    return result


def _source_runs(root: Path, snapshots: list[dict]) -> list[dict]:
    by_manifest = {(row["manifest_path"], row["source"]): row for row in snapshots}
    runs = []
    for path in archive_files(root):
        relative = path.relative_to(root).as_posix()
        if not relative.startswith("data/raw/") or path.name != "_manifest.json":
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        day = manifest["snapshot_date"]
        for source in SOURCES:
            leg = manifest.get("legs", {}).get(source, {})
            snapshot = by_manifest.get((relative, source))
            runs.append({"source": source, "collection_day": day,
                         "run_status": manifest.get("status"),
                         "leg_status": leg.get("status", "not_applicable"
                                               if manifest.get("status") == "complete"
                                               else "pending"),
                         "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                         "can_confirm_absence": False})
    return sorted(runs, key=lambda row: (row["source"], row["collection_day"]))


def _bound(history: list[dict], first: bool) -> tuple[str | None, str | None]:
    dates = [row["observation_date"] for row in history if row["observation_date"]]
    if not dates:
        return None, None
    day = min(dates) if first else max(dates)
    candidates = [row for row in history if row["observation_date"] == day]
    if any(row["observed_at"] is None for row in candidates):
        return day, None
    choose = min if first else max
    timestamp = choose((row["observed_at"] for row in candidates), key=_timestamp)
    return day, timestamp


def _deployments(observations: list[dict], runs: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in observations:
        if row["deployment_key"]:
            groups[row["deployment_key"]].append(row)
    latest_runs = {}
    for run in runs:
        if run["leg_status"] != "not_applicable":
            latest_runs[run["source"]] = run
    result = []
    for key, history in sorted(groups.items()):
        first_day, first_at = _bound(history, True)
        last_day, last_at = _bound(history, False)
        source_states = []
        for source in sorted({row["source"] for row in history}):
            run = latest_runs[source]
            present = any(row["source"] == source and row["snapshot_id"] == run["snapshot_id"]
                          for row in history)
            source_states.append({"source": source, "collection_day": run["collection_day"],
                                  "leg_status": run["leg_status"],
                                  "presence_state": "PRESENT" if present else "UNKNOWN"})
        result.append({
            "deployment_key": key, "provider": history[0]["provider"],
            "provider_model_id": history[0]["provider_model_id"], "canonical_model_id": None,
            "first_observed_at": first_at, "first_observed_date": first_day,
            "last_observed_at": last_at, "last_observed_date": last_day,
            "current_status": "PRESENT" if all(row["presence_state"] == "PRESENT"
                                                for row in source_states) else "UNKNOWN",
            "status_basis": "latest available source run; missing evidence remains unknown",
            "as_of_collection_day": max(row["collection_day"] for row in source_states),
            "latest_source_states": source_states, "observation_count": len(history),
            "source_snapshot_count": len({row["snapshot_id"] for row in history}),
            "source_hf_repo_ids": sorted({row["source_hf_repo_id"] for row in history
                                          if row["source_hf_repo_id"]}),
        })
    return result


def build_deployment_layer(root: Path, index: dict, recorded_at: str | None = None) -> dict:
    root = root.resolve()
    if index.get("schema_version") != 1 or index.get("indexer_version") != INDEXER_VERSION:
        raise ValueError("regenerate an index with the current indexer version")
    current = build_index(root)
    if index["snapshot_count"] != current["snapshot_count"] or index["snapshots"] != current[
        "snapshots"
    ]:
        raise ValueError("raw snapshot index is stale or altered; regenerate into a new output")
    recorded_at = recorded_at or utc_now()
    _timestamp(recorded_at)
    snapshots = [row for row in index["snapshots"] if row["source"] in SOURCES]
    observations = [observation for snapshot in snapshots
                    for observation in _observations(root, snapshot, recorded_at)]
    observations.sort(key=lambda row: (
        row["observation_date"] or "",
        _timestamp(row["observed_at"]).timestamp() if row["observed_at"] else float("-inf"),
        row["source"], row["observation_id"],
    ))
    runs = _source_runs(root, snapshots)
    deployments = _deployments(observations, runs)
    counts = Counter((row["deployment_key"], row["snapshot_id"]) for row in observations
                     if row["deployment_key"])
    return {
        "schema_version": 1, "normalizer_version": NORMALIZER_VERSION,
        "recorded_at": recorded_at, "source_index_generated_at": index["generated_at"],
        "source_index_sha256_semantic": stable_hash(index),
        "deployment_observations": observations, "provider_deployments": deployments,
        "source_runs": runs,
        "summary": {
            "observation_count": len(observations), "deployment_count": len(deployments),
            "observations_by_source": dict(Counter(row["source"] for row in observations)),
            "observations_by_provider": dict(Counter(row["provider"] for row in observations)),
            "presence_counts": dict(Counter(row["presence_state"] for row in observations)),
            "deployment_status_counts": dict(Counter(row["current_status"] for row in deployments)),
            "unidentified_observation_count": sum(row["deployment_key"] is None
                                                  for row in observations),
            "day_only_observation_count": sum(row["observed_at"] is None for row in observations),
            "multiple_rows_for_deployment_snapshot": sum(count > 1 for count in counts.values()),
            "source_snapshot_count": len(snapshots), "canonical_assignments": 0,
            "events_generated": 0, "absence_confirmation_enabled": False,
            "source_cutoffs": {source: max(run["collection_day"] for run in runs
                                           if run["source"] == source)
                               for source in SOURCES
                               if any(run["source"] == source for run in runs)},
        },
    }
