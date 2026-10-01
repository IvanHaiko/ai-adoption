"""Cache exact arXiv ID lookups from explicit HF tags via the official Atom API."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode, urlparse

import pyarrow.parquet as pq

from silver.build import canonical_arxiv

from .fetch import HttpClient
from .storage import read_json, write_gzip, write_json

API = "https://export.arxiv.org/api/query"
SOURCE_INTERFACE = "arxiv_atom_api_id_list"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
BATCH_SIZE = 50
MODERN_ID = re.compile(r"^(\d{2})(\d{2})\.(\d{4,5})$")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def references_from_silver(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    links = pq.read_table(root / "data/silver/hf_arxiv_links.parquet", columns=["arxiv_id"])
    wanted = {row["arxiv_id"] for row in links.to_pylist()}
    observations = pq.read_table(
        root / "data/silver/hf_observations.parquet", columns=["tags_json"]
    )
    originals: dict[str, set[str]] = defaultdict(set)
    malformed = set()
    for row in observations.to_pylist():
        for tag in json.loads(row["tags_json"]):
            if not isinstance(tag, str) or not tag.lower().startswith("arxiv:"):
                continue
            raw = tag.split(":", 1)[1]
            canonical = canonical_arxiv(raw)
            if canonical in wanted:
                originals[canonical].add(raw)
            elif canonical is None:
                malformed.add(raw)
    for aid in wanted:
        if not originals[aid]:
            originals[aid].add(aid)
    return {aid: sorted(originals[aid]) for aid in sorted(wanted)}, sorted(malformed)


def entry_id(entry: ET.Element) -> str | None:
    value = entry.findtext("a:id", namespaces=ATOM)
    if not value:
        return None
    return canonical_arxiv(urlparse(value).path.removeprefix("/abs/"))


def valid_arxiv_id(identifier: str) -> bool:
    """Format plus calendar/sequence validation; old archive/NNNNNNN IDs pass."""
    if not canonical_arxiv(identifier):
        return False
    modern = MODERN_ID.fullmatch(identifier)
    if not modern:
        return "/" in identifier and int(identifier.rsplit("/", 1)[1]) > 0
    year, month, sequence = map(int, modern.groups())
    return 1 <= month <= 12 and sequence > 0 and (year > 7 or year == 7 and month >= 4)


def parse_feed(body: bytes, requested: set[str]) -> set[str]:
    root = ET.fromstring(body)
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("arXiv response is not an Atom feed")
    found = set()
    for entry in root.findall("a:entry", ATOM):
        identifier = entry_id(entry)
        if identifier is None or identifier not in requested:
            raise ValueError("Atom feed contains an error or unexpected entry")
        found.add(identifier)
    return found


def resolve(root: Path, http: HttpClient | None = None, batch_size: int = BATCH_SIZE) -> dict:
    if not 1 <= batch_size <= 50:
        raise ValueError("batch size must be between 1 and 50")
    http = http or HttpClient(min_interval=3)
    calls_before = http.call_count
    folder = root / "data/enrichment/arxiv_ids"
    manifest_path = folder / "_manifest.json"
    references, malformed = references_from_silver(root)
    manifest = (
        read_json(manifest_path)
        if manifest_path.exists()
        else {"schema_version": 1, "source_interface": SOURCE_INTERFACE, "items": {}}
    )
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported arXiv ID manifest version")
    manifest["references"] = references
    manifest["malformed_references"] = malformed
    items = manifest["items"]
    for aid in references:
        if not valid_arxiv_id(aid) and (
            items.get(aid, {}).get("status") != "malformed_id"
            or items[aid].get("original_references") != references[aid]
        ):
            items[aid] = {
                "status": "malformed_id",
                "checked_at": utc_now(),
                "source_interface": "local_arxiv_id_validation",
                "original_references": references[aid],
            }
    pending = [
        aid
        for aid in references
        if items.get(aid, {}).get("status") not in ("resolved", "not_found", "malformed_id")
    ]
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        url = API + "?" + urlencode({"id_list": ",".join(batch), "max_results": len(batch)})
        fetched_at = utc_now()
        response = http.get(url)
        failure = None
        found = set()
        if response.ok:
            try:
                found = parse_feed(response.body, set(batch))
            except (ET.ParseError, ValueError) as exc:
                failure = str(exc)
        else:
            failure = response.error or f"HTTP {response.status}"
        if failure:
            for aid in batch:
                items[aid] = {
                    "status": "temporary_source_failure",
                    "fetched_at": fetched_at,
                    "source_interface": SOURCE_INTERFACE,
                    "request_url": url,
                    "http_status": response.status,
                    "error": failure,
                    "original_references": references[aid],
                }
        else:
            digest = hashlib.sha256(",".join(batch).encode()).hexdigest()[:20]
            filename = f"batch-{digest}.atom.xml.gz"
            stored = write_gzip(folder / filename, response.body)
            for aid in batch:
                items[aid] = {
                    "status": "resolved" if aid in found else "not_found",
                    "fetched_at": fetched_at,
                    "source_interface": SOURCE_INTERFACE,
                    "request_url": url,
                    "http_status": 200,
                    "response_file": filename,
                    "sha256_raw": stored["sha256_raw"],
                    "original_references": references[aid],
                }
        write_json(manifest_path, manifest)
    # Even a no-op run records newly found malformed tags; existing successful
    # responses and their fetched_at timestamps remain byte-identical.
    if not pending and (not manifest_path.exists() or read_json(manifest_path) != manifest):
        write_json(manifest_path, manifest)
    counts = {
        status: sum(x.get("status") == status for x in items.values())
        for status in ("resolved", "not_found", "temporary_source_failure")
    }
    counts["malformed_id"] = len(malformed) + sum(
        item.get("status") == "malformed_id" for item in items.values()
    )
    return {
        "known_canonical_ids": len(references),
        "status_counts": counts,
        "http_calls": http.call_count - calls_before,
        "manifest": str(manifest_path),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="collector.arxiv_ids")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    result = resolve(args.root, batch_size=args.batch_size)
    print(json.dumps(result, indent=2))
    return 1 if result["status_counts"]["temporary_source_failure"] else 0


if __name__ == "__main__":
    sys.exit(main())
