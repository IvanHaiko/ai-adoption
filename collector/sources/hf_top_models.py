"""The HuggingFace text-generation leaderboard by downloads, top N.

Why this leg exists at all. The catalogue leg (`hf_models`) only asks about
repositories OpenRouter names, which was 152 on the day this was written -
enough to describe what OpenRouter serves, nowhere near enough to measure
adoption. The ranking measured on 2026-08-29 is a steep power law:

     top    100   60.3% of all downloads in the category
     top  1 000   92.4%
     top  2 000   95.6%
     top  5 000   97.9%
     top 10 000   98.9%   (the 10 000th repo: 635 downloads in 30 days)

TOP_N is set from that table and nothing else. Publish the coverage figure
alongside any share computed from this leg; re-measure before quoting it,
because the distribution moves.

Two things to know before using the data.

`downloads` is a rolling 30-day figure, not a daily increment. Differencing it
gives a noisy pseudo-daily series; the shape of a release curve survives that,
a precise daily count does not.

Rank counts *repositories*, not models. Much of the tail is quantisations and
re-uploads of the same weights - `unsloth/...`, `...-GGUF`, `mradermacher/...`
- so a model's real adoption is spread across derivatives. Rolling them back up
to a canonical model through the `base_model:` tag is a Silver problem, and the
reason the tail is worth collecting at all.
"""
from __future__ import annotations

import json
import re

LEG = "hf_top_models"

API = "https://huggingface.co/api/models"
FILTER = "text-generation"
PAGE_SIZE = 1000
TOP_N = 5000

FIRST_URL = f"{API}?sort=downloads&direction=-1&limit={PAGE_SIZE}&filter={FILTER}"

# The Hub advertises the next cursor in a Link header, RFC 5988 style.
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def next_url(headers: dict) -> str | None:
    match = NEXT_LINK.search(headers.get("link", ""))
    return match.group(1) if match else None


def _line(page: int, url: str, fetched_at: str, body: bytes) -> bytes:
    """One JSONL record per page: an envelope around the response bytes.

    A page is a JSON array of ~1000 repositories. It is concatenated, never
    re-serialised, so what lands on disk is exactly what the Hub sent.
    """
    envelope = f'{{"page":{page},"url":"{url}","fetched_at":"{fetched_at}","http_status":200'
    return envelope.encode("utf-8") + b',"body":' + body.strip() + b"}"


def collect(ctx, previous: dict) -> dict:
    """Fetch the whole ranking in one pass, or leave the leg for the next run.

    Deliberately not resumable page by page, unlike `hf_models`. The ranking
    reorders continuously, so page 3 fetched an hour after pages 1 and 2 would
    silently duplicate and drop repositories - an internally inconsistent
    snapshot that looks complete. All pages in one pass, or none.
    """
    url = FIRST_URL
    lines: list[bytes] = []
    rows = 0
    page = 0

    while url and rows < TOP_N:
        page += 1
        response = ctx.http.get(url)
        if not response.ok:
            return {
                "status": "partial",
                "api": API,
                "filter": FILTER,
                "target_rows": TOP_N,
                "failed_on_page": page,
                "http_status": response.status,
                "error": response.error or f"HTTP {response.status}",
                "attempted_at": ctx.now(),
            }
        lines.append(_line(page, url, ctx.now(), response.body))
        rows += len(json.loads(response.body))
        url = next_url(response.headers)

    payload = b"\n".join(lines) + (b"\n" if lines else b"")
    record = ctx.store(LEG, payload)
    record.update(
        {
            "status": "ok",
            "api": API,
            "filter": FILTER,
            "target_rows": TOP_N,
            "pages": page,
            "rows": rows,
            # False when the Hub ran out of repositories before TOP_N, which is
            # a fact about the category, not a failure.
            "reached_target": rows >= TOP_N,
            "completed_at": ctx.now(),
        }
    )
    return record
