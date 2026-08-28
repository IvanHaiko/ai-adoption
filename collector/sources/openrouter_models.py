"""The OpenRouter model catalogue. The spine of the whole snapshot: the HF leg
reads its list of repositories out of this file rather than off the network.
"""
from __future__ import annotations

URL = "https://openrouter.ai/api/v1/models"

LEG = "openrouter_models"


def collect(ctx) -> dict:
    response = ctx.http.get(URL)
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
