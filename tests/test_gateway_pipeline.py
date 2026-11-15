import datetime as dt
import json

import pytest

from gateway import pipeline
from gateway.build import archive


def _exports(root, start="2026-01-01"):
    archive(root, "openrouter", start, start, json.dumps({"data": [
        {"date": start, "model_permaslug": "a/m", "total_tokens": "90"},
        {"date": start, "model_permaslug": "other", "total_tokens": "10"}
    ]}).encode(), "https://example.test/or")
    archive(root, "vercel", start, start, json.dumps({"rows": [
        {"date": start, "group": "model", "modality": "text", "metric": metric,
         "name": name, "share_percent": value}
        for metric in ("requests", "tokens", "spend")
        for name, value in (("A", 80), ("Other", 20))
    ]}).encode(), "https://example.test/v")


def test_window_completed_utc_and_bounds():
    assert pipeline.window(None, 7, dt.date(2026, 1, 8)) == ("2026-01-01", "2026-01-07")
    with pytest.raises(ValueError):
        pipeline.window("2026-01-08", 7, dt.date(2026, 1, 8))
    with pytest.raises(ValueError):
        pipeline.window(None, 31)


def test_offline_coverage_ignores_outside_window_and_reports_missing(tmp_path, monkeypatch):
    _exports(tmp_path)
    monkeypatch.setattr(pipeline, "fetch", lambda *a: pytest.fail("offline must not fetch"))
    result = pipeline.run(tmp_path, "2026-01-01", 1, True)
    assert result["ok"]
    assert len(result["sources"]) == 4
    assert (tmp_path / "data/analytics/core_report.md").exists()
    assert len(result["curation_queue"]) == 4
    assert not pipeline.run(tmp_path, "2026-01-02", 2, True)["ok"]


def test_missing_key_does_not_prevent_other_source_fetch(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []

    def fetched(root, source, start, end):
        calls.append(source)
        _exports(root)
        return root / "data/external/vercel"

    monkeypatch.setattr(pipeline, "fetch", fetched)
    result = pipeline.run(tmp_path, "2026-01-01", 1)
    assert calls == ["vercel"]
    assert not result["ok"]
    assert result["collection"][0]["reason"] == "OPENROUTER_API_KEY is required"
    assert result["collection"][1]["status"] == "ok"
    assert result["missing_day_metrics"] == []
    report_text = (tmp_path / "data/analytics/core_report.md").read_text()
    assert "Run status: FAILED" in report_text
    assert "OPENROUTER_API_KEY is required" in report_text


def test_fresh_checkout_reports_raw_cutoff_without_gold(tmp_path):
    _exports(tmp_path)
    raw = tmp_path / "data/raw/2026-01-01"
    raw.mkdir(parents=True)
    (raw / "_manifest.json").write_text('{}')
    result = pipeline.run(tmp_path, "2026-01-01", 1, True)
    assert result["hf_raw_cutoff"] == "2026-01-01"
    assert result["hf_gold_cutoff"] is None
    assert result["hf_cutoff"] == "2026-01-01"


def test_one_fetch_failure_keeps_successful_export_and_reports_cached_data(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    calls = []

    def fetched(root, source, start, end):
        calls.append(source)
        if source == "openrouter":
            raise RuntimeError("secret-bearing details must not appear")
        _exports(root)
        return root / "data/external/vercel"

    monkeypatch.setattr(pipeline, "fetch", fetched)
    result = pipeline.run(tmp_path, "2026-01-01", 1)
    assert calls == ["openrouter", "vercel"]
    assert not result["ok"]
    assert result["collection"][0]["reason"] == "RuntimeError"
    assert result["collection"][1]["status"] == "ok"
    assert len(result["sources"]) == 4
    text = (tmp_path / "data/analytics/core_report.json").read_text()
    assert "secret-bearing" not in text


def test_build_failure_does_not_reuse_old_output(tmp_path, monkeypatch):
    _exports(tmp_path)
    assert pipeline.run(tmp_path, "2026-01-01", 1, True)["ok"]

    def fail(*args):
        raise ValueError("damaged archive")

    monkeypatch.setattr(pipeline, "build", fail)
    result = pipeline.run(tmp_path, "2026-01-01", 1, True)
    assert not result["ok"]
    assert result["build_error"] == "ValueError"
    assert result["sources"] == []


def test_percent_queue_uses_observed_day_mean_not_sum():
    rows = [{"source": "vercel", "date": day, "source_name": "A",
             "source_model_id": None, "metric": "tokens", "unit": "percent",
             "value": value, "is_other": False, "hf_repo_id": None,
             "identity_status": "ambiguous", "manifest_path": "evidence.json"}
            for day, value in (("2026-01-01", 10), ("2026-01-02", 30))]
    summaries, queue, _ = pipeline.summarize(rows, "2026-01-01", "2026-01-02")
    assert queue[0]["priority_value"] == 20
    assert queue[0]["observed_days"] == 2
    assert summaries[0]["ambiguous_rows"] == 2
