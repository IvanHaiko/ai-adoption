from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from gold.build import build


def _table(folder, name, rows):
    pq.write_table(pa.Table.from_pylist(rows) if rows else pa.table({}), folder / f"{name}.parquet")


def _fixtures(root, latest):
    silver = root / "data/silver"
    silver.mkdir(parents=True, exist_ok=True)
    history = [
        {
            "hf_repo_id": "Acme/one",
            "snapshot_date": "2026-09-01",
            "downloads_30d": 0,
            "likes": 0,
            "author": "Acme",
            "library_name": "transformers",
            "arxiv_ids": "[]",
        },
        {
            "hf_repo_id": "Acme/one",
            "snapshot_date": "2026-09-08",
            "downloads_30d": 100,
            "likes": 3,
            "author": "Acme",
            "library_name": "transformers",
            "arxiv_ids": "[]",
        },
        {
            "hf_repo_id": "Acme/one",
            "snapshot_date": latest,
            "downloads_30d": 120,
            "likes": 4,
            "author": "Acme",
            "library_name": "transformers",
            "arxiv_ids": "[]",
        },
    ]
    _table(silver, "hf_repositories", history)
    _table(
        silver,
        "hf_observations",
        [
            {"hf_repo_id": "Acme/one", "snapshot_date": date, "source_leg": "hf_top_models"}
            for date in ("2026-09-10", "2026-09-11", "2026-09-12")
            if date <= latest
        ],
    )
    _table(
        silver,
        "hf_repo_cohorts",
        [
            {
                "hf_repo_id": "Acme/one",
                "first_seen_date": "2026-09-01",
                "first_seen_source": "hf_new_models",
                "created_at": "2026-09-01T00:00:00+00:00",
                "age_at_first_seen_days": 0,
                "base_model": None,
                "initial_downloads_30d": 0,
                "initial_likes": 0,
            }
        ],
    )
    for name in ("model_platform_links", "hf_arxiv_links", "arxiv_papers"):
        _table(silver, name, [])


def test_cohort_matures_from_silver_latest_date_and_reruns_identically(tmp_path):
    _fixtures(tmp_path, "2026-09-10")
    first = build(tmp_path)
    row = pq.read_table(tmp_path / "data/gold/model_lifecycle.parquet").to_pylist()[0]
    assert first["eligible_primary"]["7"] == 1
    assert first["eligible_primary"]["14"] == 0
    assert row["downloads_30d_at_7d"] == 100
    assert row["downloads_30d_at_14d"] is None
    _fixtures(tmp_path, "2026-09-16")
    second = build(tmp_path)
    row = pq.read_table(tmp_path / "data/gold/model_lifecycle.parquet").to_pylist()[0]
    assert second["eligible_primary"]["14"] == 1
    assert row["downloads_30d_at_14d"] == 120  # first observation in N..N+1 window
    assert row["top_entry_by_14d"] is True
    assert row["sustained_top_by_14d"] is True
    assert second["dq_error_count"] == 0
    before = (tmp_path / "data/gold/model_lifecycle.parquet").read_bytes()
    assert build(tmp_path) == second
    assert (tmp_path / "data/gold/model_lifecycle.parquet").read_bytes() == before
