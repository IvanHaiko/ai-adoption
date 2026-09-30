"""Bounded OAI-PMH ListRecords harvesting; XML responses remain byte-exact in Bronze."""

from __future__ import annotations

import base64
import datetime as dt
import json
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

API = "https://oaipmh.arxiv.org/oai"
SETS = {"cs.AI": "cs:cs:AI", "cs.CL": "cs:cs:CL", "cs.LG": "cs:cs:LG"}
NS = {"o": "http://www.openarchives.org/OAI/2.0/"}
MAX_PAGES = 100


def initial_url(category: str, date_from: str, date_until: str) -> str:
    return (
        API
        + "?"
        + urlencode(
            {
                "verb": "ListRecords",
                "metadataPrefix": "arXiv",
                "set": SETS[category],
                "from": date_from,
                "until": date_until,
            }
        )
    )


def parse_page(body: bytes) -> tuple[list[dict], str | None, str | None]:
    root = ET.fromstring(body)
    error = root.find("o:error", NS)
    if error is not None:
        if error.attrib.get("code") == "noRecordsMatch":
            return [], None, "noRecordsMatch"
        raise ValueError(f"OAI error {error.attrib.get('code')}: {error.text}")
    records = root.findall("o:ListRecords/o:record", NS)
    token = root.findtext("o:ListRecords/o:resumptionToken", default="", namespaces=NS).strip()
    if root.find("o:ListRecords", NS) is None:
        raise ValueError("missing ListRecords element")
    result = []
    for record in records:
        header = record.find("o:header", NS)
        if header is None:
            raise ValueError("record missing header")
        result.append(
            {
                "identifier": header.findtext("o:identifier", namespaces=NS),
                "datestamp": header.findtext("o:datestamp", namespaces=NS),
                "deleted": header.attrib.get("status") == "deleted",
            }
        )
    return result, token or None, None


def collect_range(
    ctx, date_from: str, date_until: str, categories: tuple[str, ...] = tuple(SETS)
) -> dict:
    """All pages in one pass. Expiring OAI tokens make cross-run page resume unsafe."""
    if not categories or any(c not in SETS for c in categories):
        raise ValueError("categories must be selected from " + ", ".join(SETS))
    start, end = dt.date.fromisoformat(date_from), dt.date.fromisoformat(date_until)
    if end < start or (end - start).days > 7:
        raise ValueError("arXiv range must be ordered and at most eight calendar days")
    lines = []
    counts = {}
    for category in categories:
        url = initial_url(category, date_from, date_until)
        page = 0
        seen_tokens = set()
        count = 0
        while url:
            page += 1
            if page > MAX_PAGES:
                return {"status": "partial", "error": "page limit exceeded", "category": category}
            response = ctx.http.get(url)
            if not response.ok:
                return {
                    "status": "partial",
                    "url": url,
                    "http_status": response.status,
                    "error": response.error or f"HTTP {response.status}",
                    "category": category,
                }
            try:
                records, token, empty_reason = parse_page(response.body)
            except (ET.ParseError, ValueError) as exc:
                return {"status": "partial", "url": url, "error": str(exc), "category": category}
            count += len(records)
            lines.append(
                json.dumps(
                    {
                        "category": category,
                        "page": page,
                        "url": url,
                        "fetched_at": ctx.now(),
                        "http_status": 200,
                        "records": records,
                        "no_records_reason": empty_reason,
                        "resumption_token": token,
                        "body_base64": base64.b64encode(response.body).decode("ascii"),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if token:
                if token in seen_tokens:
                    return {
                        "status": "partial",
                        "error": "repeated resumption token",
                        "category": category,
                    }
                seen_tokens.add(token)
                url = API + "?" + urlencode({"verb": "ListRecords", "resumptionToken": token})
            else:
                url = None
        counts[category] = count
    record = ctx.store("arxiv", b"\n".join(lines) + b"\n")
    record.update(
        {
            "status": "ok",
            "api": API,
            "metadata_prefix": "arXiv",
            "date_from": date_from,
            "date_until": date_until,
            "category_counts": counts,
            "pages": len(lines),
            "rows": sum(counts.values()),
            "completed_at": ctx.now(),
        }
    )
    return record


def collect(ctx) -> dict:
    day = dt.date.fromisoformat(ctx.snapshot_date)
    if ctx.now()[:10] != ctx.snapshot_date:
        return {
            "status": "partial",
            "reason": (
                "daily arXiv snapshot date is not the UTC collection date; "
                "use arxiv_backfill for historical metadata"
            ),
        }
    if hasattr(ctx.http, "min_interval"):
        ctx.http.min_interval = max(ctx.http.min_interval, 3)
    return collect_range(ctx, (day - dt.timedelta(days=1)).isoformat(), day.isoformat())
