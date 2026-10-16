"""Offline, reproducible enrollment from completed discovery snapshots."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path

from .storage import read_gzip, read_json, write_json

DISCOVERY = ("hf_models", "hf_top_models", "hf_new_models")
SELECTION_VERSION = 2
SELECTION_SEED = "ai-model-lifecycle/hf-cohort/v2"
SELECTION_ALGORITHM = "sha256-organization-round-robin-v2"


def instant(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def registry_path(root, cohort_id):
    if not isinstance(cohort_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cohort_id):
        raise ValueError("cohort ID must contain 1–128 letters, digits, underscores or hyphens")
    return Path(root) / "data" / "cohorts" / (cohort_id + ".json")


def valid_id(value):
    return isinstance(value, str) and bool(
        re.fullmatch(
            r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)?", value
        )
    )


def counts_valid(body):
    return isinstance(body, dict) and all(
        type(body.get(k)) is int and body[k] >= 0 for k in ("downloads", "likes")
    )


def eligible(candidate):
    return (
        valid_id(candidate.get("hf_repo_id"))
        and counts_valid(candidate)
        and not candidate.get("private")
        and not candidate.get("disabled")
    )


def _sample_key(seed, domain, value):
    encoded = json.dumps([seed, domain, value], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), value


def select_members(candidates, size=1500, *, seed=SELECTION_SEED, selection_version=2):
    if type(size) is not int or size < 1:
        raise ValueError("size must be a positive integer")
    if type(selection_version) is not int or selection_version not in (1, 2):
        raise ValueError("unsupported selection_version")
    if selection_version == 2 and (not isinstance(seed, str) or not seed):
        raise ValueError("selection seed must be a nonempty string")
    # Stable duplicate choice even when caller order changes.
    unique = {}
    for c in sorted(
        (c for c in candidates if eligible(c)), key=lambda c: json.dumps(c, sort_keys=True)
    ):
        unique.setdefault(c["hf_repo_id"], dict(c))
    chosen = []

    def take(c, group):
        unique.pop(c["hf_repo_id"])
        chosen.append(dict(c, selection_group=group, active=True))

    for c in sorted(unique.values(), key=lambda c: (-c["downloads"], c["hf_repo_id"]))[: size // 3]:
        take(c, "popular")
    dated = []
    for c in unique.values():
        with suppress(ValueError, TypeError, KeyError, AttributeError):
            dated.append((instant(c["created_at"]), c))
    for _, c in sorted(dated, key=lambda x: (-x[0].timestamp(), x[1]["hf_repo_id"]))[: size // 3]:
        take(c, "recent")
    groups = {}
    for c in unique.values():
        org = c.get("author") or c["hf_repo_id"].split("/")[0]
        groups.setdefault(org, []).append(c)
    for group in groups.values():
        group.sort(
            key=lambda c: (
                _sample_key(seed, "repository", c["hf_repo_id"])
                if selection_version == 2
                else (hashlib.sha256(c["hf_repo_id"].encode()).hexdigest(), c["hf_repo_id"])
            )
        )
    organization_order = sorted(
        groups,
        key=lambda org: _sample_key(seed, "organization", org) if selection_version == 2 else org,
    )
    while len(chosen) < size and any(groups.values()):
        for org in organization_order:
            if groups[org] and len(chosen) < size:
                take(groups[org].pop(0), "remaining")
    return chosen


def envelopes(raw):
    for line in raw.splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                yield row
        except (ValueError, UnicodeDecodeError):
            continue


def envelope_bodies(envelope):
    """Accept list pages and exact-ID envelopes without accepting renames."""
    body = envelope.get("body")
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        identities = [body[k] for k in ("id", "modelId") if body.get(k) is not None]
        if identities and all(value == envelope.get("hf_id") for value in identities):
            return [body]
    return []


def validate_registry(registry):
    """Reject malformed configuration before polling any member."""
    if not isinstance(registry, dict):
        raise ValueError("registry must be an object")
    registry_path(".", registry.get("cohort_id"))
    if "selection_version" in registry and (
        type(registry["selection_version"]) is not int
        or registry["selection_version"] not in (1, SELECTION_VERSION)
    ):
        raise ValueError("unsupported registry selection_version")
    instant(registry["selected_at"])
    if not isinstance(registry["members"], list):
        raise ValueError("registry members must be an array")
    seen = set()
    for member in registry["members"]:
        if not isinstance(member, dict) or not valid_id(member.get("hf_repo_id")):
            raise ValueError("registry member must contain a valid hf_repo_id")
        if member["hf_repo_id"] in seen:
            raise ValueError("duplicate registry member hf_repo_id")
        seen.add(member["hf_repo_id"])
        if "active" in member and type(member["active"]) is not bool:
            raise ValueError("registry member active must be boolean")
        instant(member.get("enrolled_at", registry["selected_at"]))


def _make_registry(cohort_id, cutoff, selected_at, size, candidates, inputs, observed, seed):
    members = select_members(candidates, size, seed=seed)
    for member in members:
        member["enrolled_at"] = selected_at
    canonical = json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
    return dict(
        cohort_id=cohort_id,
        selection_version=SELECTION_VERSION,
        selection_algorithm=SELECTION_ALGORITHM,
        selection_seed=seed,
        hash_algorithm="sha256",
        hash_encoding="UTF-8 compact JSON [seed, domain, value], ensure_ascii=False",
        cutoff=cutoff,
        selected_at=selected_at,
        size=size,
        selection_inputs=inputs,
        selection_inputs_sha256=hashlib.sha256(
            json.dumps(inputs, sort_keys=True).encode()
        ).hexdigest(),
        eligible_candidates=candidates,
        eligible_candidates_sha256=hashlib.sha256(canonical).hexdigest(),
        population_counts={
            "observed": observed,
            "eligible": len(candidates),
            "selected": len(members),
        },
        members=members,
    )


def initialize(
    root, cohort_id, cutoff, size=1500, selected_at=None, *, seed=SELECTION_SEED, from_cohort=None
):
    path = registry_path(root, cohort_id)
    if path.exists():
        return read_json(path)
    selected_at = selected_at or dt.datetime.now(dt.timezone.utc).isoformat()
    limit, selected = instant(cutoff), instant(selected_at)
    if limit > selected:
        raise ValueError("cutoff must be <= selected_at")
    if from_cohort is not None:
        source = read_json(registry_path(root, from_cohort))
        validate_registry(source)
        if source["cohort_id"] != from_cohort or source.get("selection_version") != 1:
            raise ValueError("v2 correction must derive from the named v1 registry")
        if instant(source["cutoff"]) != limit or source["size"] != size:
            raise ValueError("derived cohort must preserve the source cutoff and size")
        candidates, inputs = source["eligible_candidates"], source["selection_inputs"]
        canonical = json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != source["eligible_candidates_sha256"]:
            raise ValueError("source eligible candidate digest does not match")
        if (
            hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
            != source["selection_inputs_sha256"]
        ):
            raise ValueError("source discovery input digest does not match")
        registry = _make_registry(
            cohort_id,
            source["cutoff"],
            selected_at,
            size,
            candidates,
            inputs,
            source["population_counts"]["observed"],
            seed,
        )
        registry["derived_from"] = {
            "cohort_id": from_cohort,
            "registry_sha256": hashlib.sha256(
                json.dumps(
                    source, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "registry_digest_encoding": "UTF-8 compact sorted-key JSON, ensure_ascii=False",
        }
        write_json(path, registry)
        return registry
    latest, days, inputs = {}, {}, []
    for manifest_path in sorted((Path(root) / "data/raw").glob("*/_manifest.json")):
        manifest = read_json(manifest_path)
        for rank, leg in enumerate(DISCOVERY):
            if manifest.get("legs", {}).get(leg, {}).get("status") != "ok":
                continue
            source = manifest_path.parent / (leg + ".jsonl.gz")
            if not source.exists():
                continue
            raw = read_gzip(source)
            used = False
            for envelope in envelopes(raw):
                try:
                    fetched = instant(envelope["fetched_at"])
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue
                if fetched > limit or envelope.get("http_status") != 200:
                    continue
                for body in envelope_bodies(envelope):
                    rid = body.get("id") or body.get("modelId")
                    if not valid_id(rid):
                        continue
                    used = True
                    days.setdefault(rid, set()).add(fetched.date().isoformat())
                    key = (fetched, -rank)
                    if rid not in latest or key > latest[rid][0]:
                        latest[rid] = (
                            key,
                            dict(
                                hf_repo_id=rid,
                                downloads=body.get("downloads"),
                                likes=body.get("likes"),
                                created_at=body.get("createdAt"),
                                author=body.get("author"),
                                private=body.get("private"),
                                disabled=body.get("disabled"),
                            ),
                        )
            if used:
                inputs.append(
                    {
                        "path": str(source.relative_to(root)),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
    candidates = []
    for rid, (_, c) in sorted(latest.items()):
        try:
            if c["created_at"] and instant(c["created_at"]) > limit:
                continue
        except (ValueError, TypeError, AttributeError):
            c["created_at"] = None
        c["historical_observation_days"] = len(days[rid])
        if eligible(c):
            candidates.append(c)
    if not candidates:
        raise ValueError("no eligible completed HF discovery observations before cutoff")
    registry = _make_registry(
        cohort_id, cutoff, selected_at, size, candidates, inputs, len(latest), seed
    )
    write_json(path, registry)
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--selected-at")
    parser.add_argument("--size", type=int, default=1500)
    parser.add_argument("--seed", default=SELECTION_SEED)
    parser.add_argument(
        "--from-cohort", help="derive v2 from a frozen v1 registry, without reading Raw"
    )
    args = parser.parse_args()
    initialize(
        args.root,
        args.cohort_id,
        args.cutoff,
        args.size,
        args.selected_at,
        seed=args.seed,
        from_cohort=args.from_cohort,
    )


if __name__ == "__main__":
    main()
