from __future__ import annotations

import json

import pyarrow.parquet as pq

from collector.fetch import Response
from collector.run import collect
from silver.build import build
from tests.conftest import FakeHttp


class OverlapHttp(FakeHttp):
    def _ranking_page(self, url):
        response = super()._ranking_page(url)
        if "sort=downloads" in url and "cursor=" not in url:
            rows = json.loads(response.body)
            rows[0]["id"] = "Vendor/Alpha"
            rows[0]["modelId"] = "Vendor/Alpha"
            return Response(url, 200, json.dumps(rows).encode(), headers=response.headers)
        return response


def test_silver_replay_preserves_legs_and_canonical_precedence(tmp_path, clock):
    collect(tmp_path, "2026-08-29", OverlapHttp(), clock)
    report = build(tmp_path)
    out = tmp_path / "data" / "silver"
    observations = pq.read_table(out / "hf_observations.parquet").to_pylist()
    canonical = pq.read_table(out / "hf_repositories.parquet").to_pylist()
    links = pq.read_table(out / "model_platform_links.parquet").to_pylist()
    cohorts = pq.read_table(out / "hf_repo_cohorts.parquet").to_pylist()
    assert len(observations) > len(canonical)
    assert len(canonical) == len({r["hf_repo_id"] for r in canonical})
    assert (
        next(r for r in canonical if r["hf_repo_id"] == "Vendor/Alpha")["source_leg"] == "hf_models"
    )
    assert (
        next(r for r in cohorts if r["hf_repo_id"] == "Vendor/Alpha")["first_seen_source"]
        == "hf_models"
    )
    assert {r["source_entity"] for r in links} == {
        "vendor/alpha",
        "vendor/alpha-free",
        "vendor/beta",
        "vendor/closed",
    }
    assert report["duplicate_hf_within_leg"] == 0
    assert report["errors_count"] == 0
