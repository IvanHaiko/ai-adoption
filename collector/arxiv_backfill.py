"""Explicit retrospective OAI datestamp harvest, stored apart from daily observations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fetch import HttpClient
from .run import utc_now
from .sources.arxiv import SETS, collect_range
from .storage import write_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="collector.arxiv_backfill")
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-until", required=True)
    parser.add_argument("--category", choices=SETS, action="append", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    categories = tuple(dict.fromkeys(args.category))
    path = args.root / "data" / "backfill" / "arxiv" / (args.date_from + "_" + args.date_until)
    path /= "_".join(categories)
    manifest_path = path / "_manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text())["leg"]["status"] == "ok":
        raise SystemExit(f"backfill already exists: {manifest_path}")
    http = HttpClient(min_interval=3)
    from .storage import write_gzip

    class BackfillContext:
        now = staticmethod(utc_now)

        def __init__(self, http):
            self.http = http

        def store(self, _leg, data):
            return write_gzip(path / "arxiv.jsonl.gz", data)

    ctx = BackfillContext(http)
    record = collect_range(ctx, args.date_from, args.date_until, categories)
    write_json(
        manifest_path,
        {"mode": "retrospective_oai_datestamp_backfill", "collected_at": utc_now(), "leg": record},
    )
    print(f"arxiv backfill: {record['status']} -> {path}")
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
