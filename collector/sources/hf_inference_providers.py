"""Current HF text-generation inference-provider mappings, via a bounded bulk list.

The list includes only models currently returned by the provider filter. A model
missing on a later day is unknown, not proof of provider removal. Each successful
day is one complete pass: shifting cursors must never be resumed across runs.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from .hf_list import API, next_url

LEG = "hf_inference_providers"
FIRST_URL = (
    f"{API}?inference_provider=all&pipeline_tag=text-generation"
    "&limit=1000&expand=inferenceProviderMapping"
)
MAX_PAGES = 20
MAX_ROWS = 20_000


def collect(ctx) -> dict:
    url = FIRST_URL
    lines = []
    pages = rows = mappings = 0
    while url:
        if pages >= MAX_PAGES or rows >= MAX_ROWS:
            return _partial(ctx, pages, rows, "bounded page/row limit reached")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "huggingface.co"
            or parsed.path != "/api/models"
        ):
            return _partial(ctx, pages, rows, "unexpected pagination URL")
        response = ctx.http.get(url)
        if not response.ok:
            return _partial(ctx, pages, rows, response.error or f"HTTP {response.status}",
                            response.status)
        try:
            models = json.loads(response.body)
            if not isinstance(models, list):
                raise ValueError("response is not an array")
            for model in models:
                if not isinstance(model, dict) or not model.get("id"):
                    raise ValueError("model has no id")
                if not isinstance(model.get("inferenceProviderMapping"), list):
                    raise ValueError("model has no provider mapping array")
                for mapping in model["inferenceProviderMapping"]:
                    if not isinstance(mapping, dict) or not mapping.get("provider"):
                        raise ValueError("mapping has no provider")
                mappings += len(model["inferenceProviderMapping"])
        except (ValueError, TypeError) as exc:
            return _partial(ctx, pages, rows, f"invalid response schema: {exc}")
        pages += 1
        rows += len(models)
        envelope = (
            json.dumps({"page": pages, "url": url, "fetched_at": ctx.now(),
                        "http_status": response.status}, separators=(",", ":")).encode()[:-1]
            + b',"body":' + response.body.strip() + b"}"
        )
        lines.append(envelope)
        url = next_url(response.headers)
    if not rows:
        return _partial(ctx, pages, rows, "empty provider-filtered catalogue")
    record = ctx.store(LEG, b"\n".join(lines) + b"\n")
    return record | {
        "status": "ok", "api": API, "request_url": FIRST_URL,
        "scope": "currently listed text-generation models with >=1 inference provider",
        "pages": pages, "rows": rows, "mappings": mappings,
        "max_pages": MAX_PAGES, "max_rows": MAX_ROWS,
        "schema_version": 1,
    }


def _partial(ctx, pages: int, rows: int, error: str, status: int | None = None) -> dict:
    return {
        "status": "partial", "api": API, "request_url": FIRST_URL,
        "pages_attempted": pages, "rows_seen": rows,
        "http_status": status, "error": error, "attempted_at": ctx.now(),
    }
