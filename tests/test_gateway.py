import gzip
import hashlib
import json

import pytest

from gateway.build import archive, build, fetch, fetch_catalog, normalize, validate_range


def _body(value=60):
    return json.dumps({"rows": [
        {"date": "2026-01-01", "group": "model", "modality": "text",
         "name": name, "metric": metric, "share_percent": share}
        for metric in ("requests", "tokens", "spend")
        for name, share in (("Beam", value), ("Other", 100-value))
    ]}).encode()


def _meta(body):
    return {"source": "vercel", "start_date": "2026-01-01", "end_date": "2026-01-02",
            "fetched_at": "2026-02-01T00:00:00+00:00",
            "sha256": hashlib.sha256(body).hexdigest()}


def test_normalize_reports_gaps_and_preserves_other():
    rows, missing = normalize(_body(), _meta(_body()))
    assert len(rows) == 6
    assert len(missing) == 3
    assert all(r["date"] == "2026-01-02" for r in missing)
    assert all(r["source_model_id"] is None for r in rows if r["is_other"])
    assert rows[0]["hf_repo_id"] is None


def test_reject_duplicate_and_broken_denominator():
    data = json.loads(_body())
    data["rows"].append(data["rows"][0])
    with pytest.raises(ValueError, match="duplicate"):
        normalize(json.dumps(data), _meta(_body()))
    data["rows"].pop()
    data["rows"][0]["share_percent"] = 10
    with pytest.raises(ValueError, match="sum"):
        normalize(json.dumps(data), _meta(_body()))


def test_archive_repeat_revision_damage_and_coverage(tmp_path):
    first = archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(), "https://x")
    assert archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(), "https://x") == first
    second = archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(70), "https://x")
    assert second != first
    result = build(tmp_path)
    assert result["revisions"] == 2
    assert result["rows"] == 6
    assert all(r["attributed_fraction"] == 0 for r in result["coverage"])
    (first / "response.json").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="damaged"):
        build(tmp_path)


def test_openrouter_tokens_and_curated_identity(tmp_path):
    body = json.dumps({"data": [
        {"date": "2026-01-01", "model_permaslug": "author/model-version", "total_tokens": "90"},
        {"date": "2026-01-01", "model_permaslug": "other", "total_tokens": 10}],
        "meta": {"as_of": "2026-01-02T00:00:00Z"}}).encode()
    archive(tmp_path, "openrouter", "2026-01-01", "2026-01-01", body, "https://x")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([{"source": "openrouter",
                                  "source_model_id": "author/model-version",
                                  "valid_from": "2026-01-01", "valid_until": "2026-01-01",
                                  "hf_repo_id": "Author/Model", "reviewer": "test",
                                  "evidence_url": "https://example.com/model"}]))
    report = build(tmp_path, ledger)
    assert report["coverage"][0]["attributed_fraction"] == 0.9
    links = json.loads(ledger.read_text())
    links[0].pop("source_model_id")
    links[0]["source_name"] = "author/model-version"
    ledger.write_text(json.dumps(links))
    assert build(tmp_path, ledger)["coverage"][0]["attributed_fraction"] == 0


def test_credentials_required_and_completed_range(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="KEY"):
        fetch(tmp_path, "openrouter", "2026-01-01", "2026-01-01")
    with pytest.raises(ValueError, match="completed"):
        validate_range("2026-01-01", "9999-01-01")


@pytest.mark.parametrize('value', ['-1', '1.5', 'NaN', True, 1.5])
def test_openrouter_rejects_noninteger_tokens(value):
    body = json.dumps({'data': [
        {'date': '2026-01-01', 'model_permaslug': 'other', 'total_tokens': value}]}).encode()
    meta = _meta(body)
    meta['source'] = 'openrouter'
    with pytest.raises(ValueError, match='nonnegative integer'):
        normalize(body, meta)


def test_newer_revision_removes_old_top_entry(tmp_path):
    first = archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(), "https://x")
    data = json.loads(_body())
    for item in data["rows"]:
        if item["name"] == "Beam":
            item["name"] = "Replacement"
    later = archive(tmp_path, "vercel", "2026-01-01", "2026-01-01",
                    json.dumps(data).encode(), "https://x")
    for path, timestamp in ((first, "2026-02-01T00:00:00Z"),
                            (later, "2026-02-02T00:00:00Z")):
        meta_path = path / "manifest.json"
        meta = json.loads(meta_path.read_text())
        meta["fetched_at"] = timestamp
        meta_path.write_text(json.dumps(meta))
    assert build(tmp_path)["rows"] == 6
    import pyarrow.parquet as pq
    rows = pq.read_table(tmp_path / "data/analytics/gateway_usage.parquet").to_pylist()
    assert {r["source_name"] for r in rows} == {"Replacement", "Other"}


def test_bounded_catalogues_reject_untrusted_url_segments(tmp_path):
    with pytest.raises(ValueError, match="1–50"):
        fetch_catalog(tmp_path, "openrouter-endpoints", [])
    with pytest.raises(ValueError, match="invalid"):
        fetch_catalog(tmp_path, "openrouter-endpoints", ["author/model?key=secret"])


def test_reversion_is_a_new_observation_and_incomplete_write_is_refused(tmp_path):
    first = archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(), "https://x")
    archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(70), "https://x")
    reverted = archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(), "https://x")
    assert reverted != first
    assert archive(tmp_path, "vercel", "2026-01-01", "2026-01-01",
                   _body(), "https://x") == reverted
    build(tmp_path)
    import pyarrow.parquet as pq
    rows = pq.read_table(tmp_path / "data/analytics/gateway_usage.parquet").to_pylist()
    assert next(r["value"] for r in rows if r["source_name"] == "Beam") == 60
    (reverted / "manifest.json").unlink()
    with pytest.raises(ValueError, match="incomplete archive"):
        archive(tmp_path, "vercel", "2026-01-01", "2026-01-01", _body(), "https://x")


def test_missing_other_is_not_a_complete_denominator():
    data = json.loads(_body())
    data["rows"] = [r for r in data["rows"] if r["name"] != "Other"]
    for row in data["rows"]:
        row["share_percent"] = 100
    with pytest.raises(ValueError, match="missing Other"):
        normalize(json.dumps(data), _meta(_body()))


def _catalogue(root, day, targets):
    path = root / 'data/raw' / day / 'openrouter_models.json.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        json.dump({'data': [{'canonical_slug': 'author/version', 'hugging_face_id': target}
                            for target in targets]}, handle)


@pytest.mark.parametrize('conflict', [False, True])
def test_source_identity_uses_only_same_day_with_conflicts(tmp_path, conflict):
    import pyarrow.parquet as pq
    body = json.dumps({'data': [
        {'date': day, 'model_permaslug': slug, 'total_tokens': tokens}
        for day in ('2026-01-01', '2026-01-02', '2026-01-03')
        for slug, tokens in (('author/version', '90'), ('other', '10'))]}).encode()
    archive(tmp_path, 'openrouter', '2026-01-01', '2026-01-03', body, 'https://x')
    _catalogue(tmp_path, '2026-01-01', ['Org/One'])
    _catalogue(tmp_path, '2026-01-02', ['Org/Two'])
    ledger = tmp_path / 'links.json'
    ledger.write_text(json.dumps([{
        'source': 'openrouter', 'source_model_id': 'author/version',
        'valid_from': '2026-01-01', 'valid_until': '2026-01-01',
        'hf_repo_id': 'Org/Conflicting' if conflict else 'Org/One',
        'reviewer': 'test', 'evidence_url': 'https://example.com'}]))
    report = build(tmp_path, ledger)
    rows = pq.read_table(tmp_path / 'data/analytics/gateway_usage.parquet').to_pylist()
    model = {r['date']: r for r in rows if not r['is_other']}
    assert model['2026-01-01']['hf_repo_id'] == (None if conflict else 'Org/One')
    assert model['2026-01-01']['identity_status'] == (
        'ambiguous' if conflict else 'source_explicit')
    assert model['2026-01-02']['hf_repo_id'] == 'Org/Two'
    assert model['2026-01-03']['identity_status'] == 'unresolved'
    assert model['2026-01-03']['hf_repo_id'] is None
    assert json.loads(model['2026-01-02']['identity_evidence'])[0]['sha256_file']
    assert all(r['other_rows'] == 1 for r in report['coverage'])
    assert report['coverage'][0]['ambiguous_rows'] == int(conflict)
    assert report['coverage'][2]['unresolved_rows'] == 1


def test_conflicting_source_declarations_do_not_assign_identity(tmp_path):
    import pyarrow.parquet as pq
    body = json.dumps({'data': [
        {'date': '2026-01-01', 'model_permaslug': slug, 'total_tokens': tokens}
        for slug, tokens in (('author/version', 90), ('other', 10))]}).encode()
    archive(tmp_path, 'openrouter', '2026-01-01', '2026-01-01', body, 'https://x')
    _catalogue(tmp_path, '2026-01-01', ['Org/One', 'Org/Two'])
    assert build(tmp_path)['coverage'][0]['ambiguous_rows'] == 1
    row = pq.read_table(tmp_path / 'data/analytics/gateway_usage.parquet').to_pylist()[0]
    assert row['hf_repo_id'] is None


@pytest.mark.parametrize('latest_complete', [False, True])
def test_missing_metrics_follow_selected_revision_only(tmp_path, latest_complete):
    partial = _body()
    data = json.loads(partial)
    second_day = [{**row, 'date': '2026-01-02'} for row in data['rows']]
    complete = json.dumps({'rows': data['rows'] + second_day}).encode()
    older, newer = (partial, complete) if latest_complete else (complete, partial)
    archive(tmp_path, 'vercel', '2026-01-01', '2026-01-02', older, 'https://x')
    archive(tmp_path, 'vercel', '2026-01-01', '2026-01-02', newer, 'https://x')
    report = build(tmp_path)
    assert len(report['missing_day_metrics']) == (0 if latest_complete else 3)
    assert report['rows'] == (12 if latest_complete else 6)
