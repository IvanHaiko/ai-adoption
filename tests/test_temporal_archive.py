"""Archive preservation, independent metadata replay, and continued v1 readability."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.run import collect
from collector.storage import write_gzip, write_json
from temporal.__main__ import main
from temporal.archive import PROTECTED_ROOTS, fingerprint, verify, write_new_json
from temporal.index import build_index


@pytest.fixture
def archive(tmp_path):
    daily = tmp_path / "data/raw/2026-09-10"
    leg = write_gzip(daily / "openrouter_models.json.gz", b'{"data":[{"id":"org/model"}]}')
    write_json(daily / "_manifest.json", {
        "schema_version": 1, "collector_version": "0.1.0", "status": "complete",
        "snapshot_date": "2026-09-10", "legs": {"openrouter_models": leg | {
            "status": "ok", "fetched_at": "2026-09-10T12:34:56+00:00",
        }},
    })
    backfill = tmp_path / "data/backfill/arxiv/2026-09-01_2026-09-07/cs.AI"
    payload = json.dumps({"fetched_at": "2026-09-30T22:41:00+00:00", "records": []})
    leg = write_gzip(backfill / "arxiv.jsonl.gz", (payload + "\n").encode())
    write_json(backfill / "_manifest.json", {
        "collected_at": "2026-09-30T22:42:00+00:00", "leg": leg | {
            "status": "ok", "date_from": "2026-09-01", "date_until": "2026-09-07",
        },
    })
    enrichment = tmp_path / "data/enrichment/arxiv_ids"
    leg = write_gzip(enrichment / "batch-test.atom.xml.gz", b"<feed />")
    write_json(enrichment / "_manifest.json", {
        "schema_version": 1, "items": {"2609.00001": {
            "status": "resolved", "response_file": leg["file"], "sha256_raw": leg["sha256_raw"],
            "fetched_at": "2026-09-30T23:01:00.123+00:00",
        }},
    })
    write_gzip(tmp_path / "data/spikes/replicate/sample.json.gz", b"{}")
    return tmp_path


def test_baseline_freezes_all_approved_files_and_excludes_spikes(archive):
    baseline = fingerprint(archive)
    assert baseline["protected_roots"] == list(PROTECTED_ROOTS)
    assert baseline["file_count"] == 6
    assert sum(row["size_bytes"] for row in baseline["files"]) == baseline["total_bytes"]
    assert baseline["min_timestamp"] == "2026-09-10T12:34:56+00:00"
    assert not any("spikes" in row["snapshot_path"] for row in baseline["files"])
    assert verify(archive, baseline)["ok"]


def test_verification_detects_payload_and_manifest_changes_deletions_and_additions(archive):
    baseline = fingerprint(archive)
    changed = archive / "data/raw/2026-09-10/openrouter_models.json.gz"
    payload = changed.read_bytes()
    changed.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])  # same size, different SHA-256
    manifest = archive / "data/raw/2026-09-10/_manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    removed = archive / "data/enrichment/arxiv_ids/batch-test.atom.xml.gz"
    removed.unlink()
    added = archive / "data/raw/2026-09-11/new.gz"
    write_gzip(added, b"new")
    result = verify(archive, baseline)
    assert not result["ok"]
    assert result["changed"] == [manifest.relative_to(archive).as_posix(),
                                  changed.relative_to(archive).as_posix()]
    assert result["missing"] == [removed.relative_to(archive).as_posix()]
    assert result["added"] == [added.relative_to(archive).as_posix()]


def test_legitimate_archive_additions_do_not_invalidate_frozen_paths(archive):
    baseline = fingerprint(archive)
    write_gzip(archive / "data/raw/2026-09-11/new.gz", b"new")
    result = verify(archive, baseline)
    assert result["ok"] and len(result["added"]) == 1


def test_outputs_cannot_overwrite_protected_files_v1_or_existing_baseline(archive):
    before = fingerprint(archive)
    for name in ("data/raw/2026-09-10/_manifest.json", "data/backfill/arxiv/new.json",
                 "data/enrichment/arxiv_ids/new.json", "data/silver/new.json", "data/gold/x.json",
                 "data/temporal_v2/../../raw/escape.json"):
        with pytest.raises(ValueError):
            write_new_json(archive, Path(name), {})
    output = Path("data/validation/historical_archive_baseline.json")
    write_new_json(archive, output, before)
    with pytest.raises(FileExistsError):
        write_new_json(archive, output, {})
    assert verify(archive, before)["ok"]


def test_index_is_repeatable_and_preserves_capture_times_and_bytes(archive):
    baseline = fingerprint(archive)
    first = build_index(archive, generated_at="2026-10-03T10:00:00+00:00")
    second = build_index(archive, generated_at="2026-10-04T10:00:00+00:00")
    assert first["snapshots"] == second["snapshots"]
    assert first["snapshot_count"] == 3
    rows = {row["source"]: row for row in first["snapshots"]}
    assert rows["openrouter_models"]["collected_at"] == "2026-09-10T12:34:56+00:00"
    assert rows["arxiv"]["collected_at"] == "2026-09-30T22:41:00+00:00"
    assert rows["arxiv"]["collection_date"] == "2026-09-30"
    assert rows["arxiv"]["source_scope"]["date_from"] == "2026-09-01"
    assert rows["arxiv_ids"]["observed_precision"] == "fractional_seconds_3"
    assert all(row["can_confirm_absence"] is False for row in rows.values())
    assert all(row["pagination_complete"] is None for row in rows.values())
    assert verify(archive, baseline)["ok"]


def test_missing_capture_time_retains_only_day_precision(archive):
    path = archive / "data/raw/2026-09-10/_manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["legs"]["openrouter_models"]["fetched_at"]
    write_json(path, manifest)
    row = next(row for row in build_index(archive)["snapshots"]
               if row["source"] == "openrouter_models")
    assert row["collected_at"] is None
    assert row["collection_date"] == "2026-09-10"
    assert row["observed_precision"] == "day"


def test_index_rejects_corruption_even_without_baseline(archive):
    write_gzip(archive / "data/raw/2026-09-10/openrouter_models.json.gz", b'{"data":[]}')
    with pytest.raises(ValueError, match="hash mismatch"):
        build_index(archive)


def test_multi_response_capture_interval_and_partial_status_are_preserved(archive):
    folder = archive / "data/raw/2026-09-10"
    pages = [
        {"fetched_at": "2026-09-10T23:59:59+00:00", "body": [{"id": "org/a"}]},
        {"fetched_at": "2026-09-11T00:00:01+00:00", "body": [{"id": "org/b"}]},
    ]
    leg = write_gzip(folder / "hf_inference_providers.jsonl.gz",
                     ("\n".join(json.dumps(page) for page in pages) + "\n").encode())
    manifest = json.loads((folder / "_manifest.json").read_text())
    manifest["status"] = "partial"
    manifest["legs"]["hf_inference_providers"] = leg | {"status": "partial"}
    write_json(folder / "_manifest.json", manifest)
    row = next(row for row in build_index(archive)["snapshots"]
               if row["source"] == "hf_inference_providers")
    assert row["collected_at"] == pages[0]["fetched_at"]
    assert row["collected_until"] == pages[1]["fetched_at"]
    assert row["collection_day"] == "2026-09-10"
    assert row["model_observation_count"] == 2
    assert row["leg_status"] == row["run_status"] == "partial"
    assert row["can_confirm_absence"] is False


def test_cli_dry_run_and_failed_verification_publish_no_index(archive, capsys):
    args = ["--root", str(archive)]
    assert main([*args, "baseline"]) == 0
    before = fingerprint(archive)
    assert main([*args, "index", "--dry-run"]) == 0
    assert not (archive / "data/temporal_v2").exists()
    assert main([*args, "index"]) == 0
    assert main([*args, "index"]) == 1  # exclusive output creation
    output = archive / "data/temporal_v2/raw_snapshot_index.json"
    saved = output.read_bytes()
    path = archive / "data/raw/2026-09-10/_manifest.json"
    path.write_bytes(path.read_bytes() + b" ")
    assert main([*args, "index", "--output", "data/temporal_v2/second.json"]) == 1
    assert not (archive / "data/temporal_v2/second.json").exists()
    assert output.read_bytes() == saved
    assert not verify(archive, before)["ok"]
    capsys.readouterr()


def test_baseline_cannot_contain_paths_outside_archive(archive):
    baseline = fingerprint(archive)
    baseline["files"][0]["snapshot_path"] = "../secret"
    with pytest.raises(ValueError, match="invalid protected baseline path"):
        verify(archive, baseline)


def test_index_detects_changes_to_files_added_since_baseline(archive, monkeypatch):
    import temporal.__main__ as cli

    write_new_json(archive, Path("data/validation/historical_archive_baseline.json"),
                   fingerprint(archive))
    added = archive / "data/raw/2026-09-10/new-evidence.txt"
    added.write_bytes(b"aa")
    original_build = cli.build_index

    def mutate_added(root):
        index = original_build(root)
        added.write_bytes(b"bb")  # same paths, counts and total size as before
        return index

    monkeypatch.setattr(cli, "build_index", mutate_added)
    assert main(["--root", str(archive), "index"]) == 1
    assert not (archive / "data/temporal_v2/raw_snapshot_index.json").exists()


def test_v1_collector_silver_and_gold_continue_after_index(tmp_path, http, clock):
    from gold.build import build as build_gold
    from silver.build import build as build_silver

    collect(tmp_path, snapshot_date="2026-08-29", http=http, now=clock)
    baseline = fingerprint(tmp_path)
    build_index(tmp_path)
    previous_calls = http.call_count
    collect(tmp_path, snapshot_date="2026-08-29", http=http, now=clock)
    assert http.call_count == previous_calls
    assert build_silver(tmp_path)["errors_count"] == 0
    assert build_gold(tmp_path)["dq_error_count"] == 0
    assert verify(tmp_path, baseline)["ok"]


def test_real_historical_baseline_and_read_only_index():
    from temporal.baselines import selected_baseline

    root = Path(__file__).resolve().parent.parent
    baseline = json.loads(selected_baseline(root).read_text(encoding="utf-8"))
    before = verify(root, baseline)
    assert before["ok"], before
    index = build_index(root)
    expected_payloads = {entry["snapshot_path"] for entry in baseline["files"]
                         if entry["snapshot_path"].endswith(".gz")}
    assert expected_payloads <= {row["snapshot_path"] for row in index["snapshots"]}
    assert verify(root, baseline) == before
