from __future__ import annotations

import base64
import json

from collector.fetch import Response
from collector.run import collect
from collector.sources.arxiv import (
    API,
    collect_range,
    initial_url,
    parse_page,
)
from collector.sources.arxiv import (
    collect as collect_arxiv,
)
from collector.storage import read_gzip
from silver.build import _arxiv, canonical_arxiv

PAGE = b"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<ListRecords><record><header><identifier>oai:arXiv.org:2501.12345</identifier>
<datestamp>2026-09-29</datestamp></header><metadata>
<arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>2501.12345</id>
<created>2025-01-20</created><updated>2025-01-21</updated><title>Test paper</title>
<authors><author><keyname>Smith</keyname><forenames>Ada</forenames></author></authors>
<categories>cs.LG cs.AI</categories><abstract>Abstract</abstract></arXiv>
</metadata></record><resumptionToken>next-token</resumptionToken></ListRecords></OAI-PMH>"""
END = b"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<ListRecords><resumptionToken></resumptionToken></ListRecords></OAI-PMH>"""


class ArxivHttp:
    def __init__(self, fail=False):
        self.urls = []
        self.fail = fail

    def get(self, url):
        self.urls.append(url)
        if self.fail:
            return Response(url, 503, error="injected")
        return Response(url, 200, PAGE if "resumptionToken" not in url else END)


class Context:
    def __init__(self, root, http):
        self.root = root
        self.http = http

    def now(self):
        return "2026-09-30T12:00:00+00:00"

    def store(self, _, data):
        from collector.storage import write_gzip

        return write_gzip(self.root / "arxiv.jsonl.gz", data)


def test_oai_response_and_pagination_are_preserved(tmp_path):
    http = ArxivHttp()
    result = collect_range(Context(tmp_path, http), "2026-09-29", "2026-09-30", ("cs.LG",))
    assert result["status"] == "ok"
    assert result["rows"] == 1 and result["pages"] == 2
    assert http.urls == [
        initial_url("cs.LG", "2026-09-29", "2026-09-30"),
        API + "?verb=ListRecords&resumptionToken=next-token",
    ]
    lines = [json.loads(x) for x in read_gzip(tmp_path / "arxiv.jsonl.gz").splitlines()]
    assert base64.b64decode(lines[0]["body_base64"]) == PAGE
    assert lines[0]["records"][0]["datestamp"] == "2026-09-29"
    assert lines[1]["records"] == [] and lines[1]["no_records_reason"] is None
    papers = _arxiv("2026-09-30", tmp_path / "arxiv.jsonl.gz", [])
    assert papers[0]["arxiv_id"] == "2501.12345"
    assert papers[0]["submitted_at"] == "2025-01-20T00:00:00"
    assert papers[0]["authors_json"] == '["Ada Smith"]'


def test_oai_failure_is_not_empty_data(tmp_path):
    result = collect_range(
        Context(tmp_path, ArxivHttp(fail=True)), "2026-09-29", "2026-09-30", ("cs.LG",)
    )
    assert result["status"] == "partial"
    assert not (tmp_path / "arxiv.jsonl.gz").exists()


def test_no_records_is_distinct_from_error():
    body = (
        b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        b'<error code="noRecordsMatch"/></OAI-PMH>'
    )
    assert parse_page(body) == ([], None, "noRecordsMatch")
    assert canonical_arxiv("2501.12345v2") == "2501.12345"


def test_old_complete_snapshot_is_not_reopened_for_arxiv(tmp_path, http, clock):
    first = collect(tmp_path, "2026-08-29", http, clock)
    calls = http.call_count
    second = collect(tmp_path, "2026-08-29", http, clock)
    assert second == first
    assert http.call_count == calls


def test_historical_date_cannot_be_mislabeled_as_daily(tmp_path):
    context = Context(tmp_path, ArxivHttp())
    context.snapshot_date = "2026-09-29"
    result = collect_arxiv(context)
    assert result["status"] == "partial"
    assert context.http.urls == []
