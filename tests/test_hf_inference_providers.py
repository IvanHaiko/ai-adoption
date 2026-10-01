"""Provider snapshots preserve the actual list response and its uncertainty."""
from __future__ import annotations

import json

from collector.fetch import Response
from collector.run import Context
from collector.sources import hf_inference_providers as source
from collector.storage import read_gzip
from silver.build import _hf_providers


class Pages:
    def __init__(self, broken_second=False):
        self.calls = 0
        self.broken_second = broken_second

    def get(self, url):
        self.calls += 1
        if self.calls == 1:
            body = b'[{"id":"org/model","inferenceProviderMapping":[' \
                   b'{"provider":"one","providerId":"x","status":"live"}]}]'
            headers = {"link": '<https://huggingface.co/api/models?cursor=next>; rel="next"'}
            return Response(url, 200, body, headers=headers)
        if self.broken_second:
            return Response(url, 503, error="unavailable")
        return Response(url, 200, b'[{"id":"org/other","inferenceProviderMapping":[]}]')


def test_complete_pass_keeps_raw_pages_and_distinguishes_empty_mapping(tmp_path):
    ctx = Context(tmp_path, "2026-10-01", Pages(), lambda: "2026-10-01T01:00:00+00:00")
    result = source.collect(ctx)
    assert result["status"] == "ok"
    assert (result["pages"], result["rows"], result["mappings"]) == (2, 2, 1)
    path = tmp_path / "data/raw/2026-10-01/hf_inference_providers.jsonl.gz"
    pages = [json.loads(line) for line in read_gzip(path).splitlines()]
    assert pages[0]["body"][0]["inferenceProviderMapping"][0]["providerId"] == "x"
    models, providers = _hf_providers("2026-10-01", path, [])
    assert [row["provider_count"] for row in models] == [1, 0]
    assert len(providers) == 1
    assert providers[0]["status"] == "live"


def test_failed_page_does_not_publish_a_partial_catalogue(tmp_path):
    ctx = Context(tmp_path, "2026-10-01", Pages(True), lambda: "2026-10-01T01:00:00+00:00")
    result = source.collect(ctx)
    assert result["status"] == "partial"
    assert result["rows_seen"] == 1
    assert not (tmp_path / "data/raw/2026-10-01/hf_inference_providers.jsonl.gz").exists()
