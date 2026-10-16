"""Bounded authenticated Replicate API sample; never stores the API token.

This is a spike, not a daily Bronze collector. It saves the exact public JSON
page bodies with request provenance for a reproducible viability assessment.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from collector.fetch import HttpClient
from collector.storage import write_gzip, write_json

API = "https://api.replicate.com/v1/models"
SORTS = {
    "newest_created": "?sort_by=model_created_at&sort_direction=desc",
    "oldest_created": "?sort_by=model_created_at&sort_direction=asc",
    "latest_version": "?sort_by=latest_version_created_at&sort_direction=desc",
}


def sample(output: Path, pages_per_sort: int = 3, token: str | None = None) -> dict:
    token = token or os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        raise ValueError("REPLICATE_API_TOKEN is not set")
    if not 1 <= pages_per_sort <= 10:
        raise ValueError("pages_per_sort must be in 1..10")
    if (output / "_manifest.json").exists():
        raise FileExistsError(f"spike already exists: {output}")
    client = HttpClient(min_interval=0.5, timeout=60)
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    summary = {"kind": "bounded_api_spike", "source": "replicate_models",
               "started_at": started_at, "pages_per_sort": pages_per_sort,
               "strata": {}, "http_calls": 0}
    for label, query in SORTS.items():
        url = API + query
        lines = []
        info = {"first_url": url, "pages": 0, "models": 0,
                "has_next_after_sample": None, "status": "partial"}
        for page in range(1, pages_per_sort + 1):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.netloc != "api.replicate.com":
                info["error"] = "unexpected pagination URL"
                break
            response = client.get(url, headers={"Authorization": f"Bearer {token}"})
            if not response.ok:
                info["http_status"] = response.status
                info["error"] = response.error or f"HTTP {response.status}"
                break
            fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                body = json.loads(response.body)
                rows = body["results"]
                if not isinstance(rows, list) or "next" not in body:
                    raise ValueError("missing results or next")
            except (KeyError, TypeError, ValueError) as exc:
                info["error"] = f"invalid response schema: {exc}"
                break
            envelope = json.dumps(
                {"page": page, "url": url, "fetched_at": fetched_at,
                 "http_status": response.status}, separators=(",", ":")
            ).encode()[:-1] + b',"body":' + response.body.strip() + b"}"
            lines.append(envelope)
            info["pages"] += 1
            info["models"] += len(rows)
            url = body["next"]
            info["has_next_after_sample"] = bool(url)
            if not url:
                break
        if lines:
            file_name = f"{label}.jsonl.gz"
            stored = write_gzip(output / file_name, b"\n".join(lines) + b"\n")
            info.update(stored)
        if "error" not in info:
            info["status"] = "sampled"
        summary["strata"][label] = info
        if "error" in info:
            break
    summary["http_calls"] = client.call_count
    summary["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(output / "_manifest.json", summary)
    return summary


def sample_collection(output: Path, token: str | None = None) -> dict:
    token = token or os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        raise ValueError("REPLICATE_API_TOKEN is not set")
    manifest_path = output / "_collection_manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"collection spike already exists: {output}")
    url = "https://api.replicate.com/v1/collections/language-models"
    response = HttpClient(min_interval=0.5).get(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    if not response.ok:
        raise RuntimeError(f"collection request failed with HTTP {response.status}")
    body = json.loads(response.body)
    if body.get("slug") != "language-models" or not isinstance(body.get("models"), list):
        raise ValueError("unexpected collection response schema")
    stored = write_gzip(output / "language_models.json.gz", response.body)
    summary = {
        "kind": "bounded_api_spike_collection", "request_url": url,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "http_status": response.status, "models": len(body["models"]),
        "schema_version": 1,
    } | stored
    write_json(manifest_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-per-sort", type=int, default=3)
    parser.add_argument("--collection-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("data/spikes/replicate") /
                        dt.datetime.now(dt.timezone.utc).date().isoformat())
    args = parser.parse_args()
    if args.collection_only:
        print(json.dumps(sample_collection(args.output), indent=2))
        return 0
    result = sample(args.output, args.pages_per_sort)
    print(json.dumps(result, indent=2))
    return 1 if any(s["status"] != "sampled" for s in result["strata"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
