"""The rankings page, stored as delivered HTML.

The usage payload is embedded in the page and its shape is OpenRouter's to
change. Parsing it is a Silver concern; Bronze keeps the page.
"""
from __future__ import annotations

URL = "https://openrouter.ai/rankings"

LEG = "openrouter_rankings"


def collect(ctx) -> dict:
    response = ctx.http.get(URL, headers={"Accept": "text/html"})
    record = {
        "url": URL,
        "fetched_at": ctx.now(),
        "http_status": response.status,
        "attempts": response.attempts,
    }
    if not response.ok:
        record["status"] = "partial"
        record["error"] = response.error or f"HTTP {response.status}"
        return record
    record.update(ctx.store(LEG, response.body))
    record["status"] = "ok"
    return record
