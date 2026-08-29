"""Rule 2 of the project, checked rather than asserted: a second run of the same
day makes no network calls and changes no bytes on disk."""
from __future__ import annotations

import hashlib

from collector import manifest as mf
from collector.run import collect

DATE = "2026-08-29"


def fingerprint(root):
    """Every file under the snapshot, path and content."""
    out = {}
    for path in sorted((root / "data" / "raw").rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_second_run_makes_no_calls_and_changes_no_bytes(tmp_path, http, clock):
    first = collect(tmp_path, snapshot_date=DATE, http=http, now=clock)
    assert first["status"] == mf.COMPLETE
    calls_after_first = http.call_count
    assert calls_after_first > 0

    before = fingerprint(tmp_path)

    second = collect(tmp_path, snapshot_date=DATE, http=http, now=clock)

    assert http.call_count == calls_after_first, "second run hit the network"
    assert fingerprint(tmp_path) == before, "second run rewrote files"
    assert second["status"] == mf.COMPLETE
    assert len(second["runs"]) == 1, "a no-op run must not append a run entry"


def test_one_repository_is_fetched_once_even_when_two_models_share_it(tmp_path, http, clock):
    collect(tmp_path, snapshot_date=DATE, http=http, now=clock)
    hf_calls = [c for c in http.calls if c.startswith("https://huggingface.co/api/models/")]
    assert len(hf_calls) == len(set(hf_calls))
    assert len(hf_calls) == 3, hf_calls  # Alpha, Beta, Gone — not the empty one
