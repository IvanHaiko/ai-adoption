"""Focused v2 selection and immutable-registry acceptance checks, offline only."""

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import pytest

from collector.cohort import initialize, registry_path, select_members, validate_registry

V1 = "hf_lifecycle_20261014_v1"
V2 = "hf_lifecycle_20261015_v2"
SEED = "ai-model-lifecycle/hf-cohort/v2"
CUTOFF = "2026-10-13T23:59:59+00:00"
SELECTED = "2026-10-15T08:37:00+00:00"
PROJECT = Path(__file__).resolve().parents[1]


def digest(seed, domain, value):
    payload = json.dumps([seed, domain, value], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fixture_candidates():
    rows = [
        dict(hf_repo_id=f"popular/{i}", downloads=10000 - i, likes=1, created_at=None)
        for i in range(4)
    ]
    rows += [
        dict(
            hf_repo_id=f"recent/{i}",
            downloads=100 - i,
            likes=1,
            created_at=f"2026-10-{13 - i:02d}T00:00:00Z",
        )
        for i in range(4)
    ]
    rows += [
        dict(hf_repo_id=f"org-{org:02d}/{i}", downloads=0, likes=0, created_at=None)
        for org in range(30)
        for i in range(5)
    ]
    return rows


def group_ids(members, group):
    return [m["hf_repo_id"] for m in members if m["selection_group"] == group]


def test_order_independence_seed_repeatability_and_no_duplicates():
    inputs = fixture_candidates()
    baseline = select_members(inputs, 12, seed=SEED)
    shuffled = inputs + [dict(inputs[0])]
    random.Random(515).shuffle(shuffled)
    assert select_members(shuffled, 12, seed=SEED) == baseline
    assert select_members(list(reversed(inputs)), 12, seed=SEED) == baseline
    assert len({m["hf_repo_id"] for m in baseline}) == 12
    assert Counter(m["selection_group"] for m in baseline) == {
        "popular": 4,
        "recent": 4,
        "remaining": 4,
    }


def test_remaining_uses_seeded_hash_order_instead_of_lexical_prefix():
    rows = fixture_candidates()
    remaining = group_ids(select_members(rows, 12, seed=SEED), "remaining")
    organizations = [f"org-{i:02d}" for i in range(30)]
    expected_orgs = sorted(organizations, key=lambda o: (digest(SEED, "organization", o), o))[:4]
    expected = [
        min(
            (f"{org}/{i}" for i in range(5)), key=lambda rid: (digest(SEED, "repository", rid), rid)
        )
        for org in expected_orgs
    ]
    assert remaining == expected
    assert {rid.split("/")[0] for rid in remaining} != set(sorted(organizations)[:4])


def test_different_seeds_change_only_remaining_members():
    a = select_members(fixture_candidates(), 12, seed=SEED)
    b = select_members(fixture_candidates(), 12, seed="alternative-independent-seed")
    assert group_ids(a, "popular") == group_ids(b, "popular")
    assert group_ids(a, "recent") == group_ids(b, "recent")
    assert group_ids(a, "remaining") != group_ids(b, "remaining")


def test_round_robin_keeps_organization_diversity_when_second_round_needed():
    rows = [
        dict(hf_repo_id=f"{org}/{i}", downloads=0, likes=0, created_at=None)
        for org in ("A", "b", "z")
        for i in range(20)
    ]
    members = select_members(rows, 18, seed=SEED)
    counts = Counter(rid.split("/")[0] for rid in group_ids(members, "remaining"))
    assert max(counts.values()) - min(counts.values()) <= 1
    assert len({m["hf_repo_id"] for m in members}) == 18


@pytest.mark.parametrize("seed", [None, "", 42, True])
def test_invalid_v2_seed_rejected(seed):
    with pytest.raises(ValueError):
        select_members(fixture_candidates(), 12, seed=seed)


def test_real_population_v1_replay_and_unchanged_popular_recent():
    source = json.loads(registry_path(PROJECT, V1).read_bytes())
    legacy = select_members(source["eligible_candidates"], 1500, selection_version=1)
    assert [(m["hf_repo_id"], m["selection_group"]) for m in legacy] == [
        (m["hf_repo_id"], m["selection_group"]) for m in source["members"]
    ]
    corrected = select_members(source["eligible_candidates"], 1500, seed=SEED)
    assert len({m["hf_repo_id"] for m in corrected}) == 1500
    assert Counter(m["selection_group"] for m in corrected) == {
        "popular": 500,
        "recent": 500,
        "remaining": 500,
    }
    for group in ("popular", "recent"):
        assert group_ids(corrected, group) == group_ids(source["members"], group)
    assert set(group_ids(corrected, "remaining")) != set(group_ids(source["members"], "remaining"))


def test_checked_in_v2_reproduces_byte_for_byte_without_reading_raw(tmp_path):
    source_path = registry_path(PROJECT, V1)
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    expected_path = registry_path(PROJECT, V2)
    expected = json.loads(expected_path.read_bytes())
    destination = registry_path(tmp_path, V1)
    destination.parent.mkdir(parents=True)
    # Different checkout line endings must not change the derived JSON bytes.
    destination.write_bytes(source_bytes.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    raw = tmp_path / "data/raw/2026-10-15/_manifest.json"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"deliberately invalid later manifest: must never be opened")
    baseline = destination.read_bytes()
    actual = initialize(
        tmp_path,
        V2,
        expected["cutoff"],
        size=1500,
        selected_at=expected["selected_at"],
        seed=expected["selection_seed"],
        from_cohort=V1,
    )
    assert registry_path(tmp_path, V2).read_bytes() == expected_path.read_bytes()
    assert actual["eligible_candidates"] == source["eligible_candidates"]
    assert actual["selection_inputs"] == source["selection_inputs"]
    assert actual["cutoff"] == source["cutoff"] == CUTOFF
    assert destination.read_bytes() == baseline
    assert source_path.read_bytes() == source_bytes
    assert initialize(tmp_path, V2, "invalid", seed="new-seed") == actual
    assert registry_path(tmp_path, V2).read_bytes() == expected_path.read_bytes()
    validate_registry(source)
    validate_registry(actual)


@pytest.mark.parametrize("tamper", ["candidates", "inputs", "cutoff", "size"])
def test_derivation_rejects_changed_frozen_inputs_or_parameters(tmp_path, tamper):
    source = json.loads(registry_path(PROJECT, V1).read_bytes())
    cutoff, size = CUTOFF, 1500
    if tamper == "candidates":
        source["eligible_candidates"][0]["downloads"] += 1
    elif tamper == "inputs":
        source["selection_inputs"][0]["sha256"] = "0" * 64
    elif tamper == "cutoff":
        cutoff = "2026-10-14T23:59:59Z"
    else:
        size = 1499
    source_path = registry_path(tmp_path, V1)
    source_path.parent.mkdir(parents=True)
    source_path.write_text(json.dumps(source), encoding="utf-8")
    before = source_path.read_bytes()
    with pytest.raises(ValueError):
        initialize(tmp_path, V2, cutoff, size=size, selected_at=SELECTED, from_cohort=V1)
    assert not registry_path(tmp_path, V2).exists()
    assert source_path.read_bytes() == before


def test_v2_eligibility_matches_v1():
    rows = fixture_candidates()
    rows += [
        dict(hf_repo_id="bad/x", downloads=0, likes=0, private=True),
        dict(hf_repo_id="bad/y", downloads=0, likes=True),
        dict(hf_repo_id="../bad", downloads=0, likes=0),
    ]
    a = select_members(rows, 1000, selection_version=1)
    b = select_members(rows, 1000, seed=SEED)
    assert {m["hf_repo_id"] for m in a} == {m["hf_repo_id"] for m in b}
    assert not any(m["hf_repo_id"].startswith(("bad/", "../")) for m in b)
