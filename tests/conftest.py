"""A fake HTTP client, so the test suite never touches the network.

It counts calls, which is the whole point of the idempotence test: the claim is
not "the second run produced the same files", it is "the second run did not ask".
"""
from __future__ import annotations

import json

import pytest

from collector.fetch import Response

MODELS_URL = "https://openrouter.ai/api/v1/models"
RANKINGS_URL = "https://openrouter.ai/rankings"
HF_API = "https://huggingface.co/api/models/"
HF_LIST = "https://huggingface.co/api/models?"


def catalogue(entries) -> bytes:
    """An OpenRouter payload shaped like the real one: the field is always
    present, and empty for most models."""
    return json.dumps({"data": [
        {"id": slug, "name": slug, "hugging_face_id": hf} for slug, hf in entries
    ]}).encode("utf-8")


DEFAULT_ENTRIES = [
    ("vendor/alpha", "Vendor/Alpha"),
    ("vendor/alpha-free", "Vendor/Alpha"),   # two models, one repository
    ("vendor/beta", "Vendor/Beta"),
    ("vendor/closed", "Vendor/Gone"),        # gated or deleted upstream
    ("vendor/hosted-only", ""),              # no paper, no weights
]


class FakeHttp:
    def __init__(self, entries=None, hf_status=None, fail_urls=None,
                 top_pages=3, page_rows=2, fail_top_page=None):
        self.entries = DEFAULT_ENTRIES if entries is None else entries
        self.hf_status = {"Vendor/Gone": 401} | (hf_status or {})
        self.fail_urls = fail_urls or {}
        # The ranking leg pages through a cursor; these keep it small in tests.
        self.top_pages = top_pages
        self.page_rows = page_rows
        self.fail_top_page = fail_top_page
        self.call_count = 0
        self.calls: list[str] = []

    def _ranking_page(self, url):
        page = int(url.split("cursor=page")[1].split("&")[0]) if "cursor=page" in url else 1
        sort = "createdAt" if "sort=createdAt" in url else "downloads"
        if page == self.fail_top_page:
            return Response(url, 503, error="injected")
        rows = [
            {"_id": f"id{sort}{page}{i}", "id": f"vendor/{sort}-{page}-{i}",
             "downloads": 1000 - page * 10 - i, "likes": i,
             "createdAt": f"2026-08-{28 - page:02d}T00:00:00.000Z",
             "tags": ["text-generation"]}
            for i in range(self.page_rows)
        ]
        headers = {}
        if page < self.top_pages:
            headers["link"] = f'<{HF_LIST}sort={sort}&cursor=page{page + 1}>; rel="next"'
        return Response(url, 200, json.dumps(rows).encode(), headers=headers)

    def get(self, url, headers=None) -> Response:
        self.call_count += 1
        self.calls.append(url)
        if url in self.fail_urls:
            return Response(url, self.fail_urls[url], error="injected")
        if url == MODELS_URL:
            return Response(url, 200, catalogue(self.entries))
        if url == RANKINGS_URL:
            return Response(url, 200, b"<html><body>rankings</body></html>")
        if url.startswith(HF_LIST):
            return self._ranking_page(url)
        if url.startswith(HF_API):
            hf_id = url[len(HF_API):]
            status = self.hf_status.get(hf_id, 200)
            if status != 200:
                return Response(url, status, error=f"HTTP {status}")
            body = json.dumps({"id": hf_id, "downloads": 1000, "likes": 7}).encode()
            return Response(url, 200, body)
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def http():
    return FakeHttp()


@pytest.fixture
def clock():
    def now():
        return "2026-08-29T00:00:00+00:00"
    return now
