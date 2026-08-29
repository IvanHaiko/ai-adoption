"""Shared cursor pagination over the HuggingFace model list endpoint.

Both list legs - the ranking by downloads and the stream of new arrivals - page
the same endpoint the same way and differ only in sort order and depth. The
walk lives here so a fix to one cannot quietly leave the other behind.
"""
from __future__ import annotations

import json
import re

API = "https://huggingface.co/api/models"
FILTER = "text-generation"
PAGE_SIZE = 1000

# The Hub advertises the next cursor in a Link header, RFC 5988 style.
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def first_url(sort: str) -> str:
    return f"{API}?sort={sort}&direction=-1&limit={PAGE_SIZE}&filter={FILTER}"


def next_url(headers: dict) -> str | None:
    match = NEXT_LINK.search(headers.get("link", ""))
    return match.group(1) if match else None


def _line(page: int, url: str, fetched_at: str, body: bytes) -> bytes:
    """One JSONL record per page: an envelope around the response bytes.

    A page is a JSON array of up to PAGE_SIZE repositories. It is concatenated,
    never re-serialised, so what lands on disk is exactly what the Hub sent.
    """
    envelope = f'{{"page":{page},"url":"{url}","fetched_at":"{fetched_at}","http_status":200'
    return envelope.encode("utf-8") + b',"body":' + body.strip() + b"}"


def collect_paged(ctx, leg: str, sort: str, target_rows: int) -> dict:
    """Fetch a whole list in one pass, or leave the leg for the next run.

    Deliberately not resumable page by page, unlike `hf_models`. Both orderings
    shift under us - the ranking reorders continuously, and a repository created
    between two pages of a newest-first walk pushes everything down by one - so
    pages fetched an hour apart would silently duplicate and drop repositories.
    That produces an internally inconsistent list which nonetheless looks
    complete. All pages in one pass, or none.
    """
    url = first_url(sort)
    lines: list[bytes] = []
    rows = page = 0

    while url and rows < target_rows:
        page += 1
        response = ctx.http.get(url)
        if not response.ok:
            return {
                "status": "partial",
                "api": API,
                "filter": FILTER,
                "sort": sort,
                "target_rows": target_rows,
                "failed_on_page": page,
                "http_status": response.status,
                "error": response.error or f"HTTP {response.status}",
                "attempted_at": ctx.now(),
            }
        lines.append(_line(page, url, ctx.now(), response.body))
        rows += len(json.loads(response.body))
        url = next_url(response.headers)

    payload = b"\n".join(lines) + (b"\n" if lines else b"")
    record = ctx.store(leg, payload)
    record.update(
        {
            "status": "ok",
            "api": API,
            "filter": FILTER,
            "sort": sort,
            "target_rows": target_rows,
            "pages": page,
            "rows": rows,
            # False when the Hub ran out of repositories before the target,
            # which is a fact about the category, not a failure.
            "reached_target": rows >= target_rows,
            "completed_at": ctx.now(),
        }
    )
    return record
