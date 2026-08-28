"""CLI: `python -m collector [--date YYYY-MM-DD] [--root PATH]`.

Exit code 0 when the day is complete, 1 when it is partial, so a scheduler can
tell the difference between "collected" and "collected some of it".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import manifest as mf
from . import paths
from .fetch import HttpClient
from .run import collect, utc_today


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collector", description=__doc__)
    parser.add_argument("--date", default=None, help="snapshot date, UTC (default: today)")
    parser.add_argument("--root", default=None, help="repository root to write under")
    parser.add_argument("--min-interval", type=float, default=0.25,
                        help="seconds between the start of consecutive requests")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else paths.repo_root()
    date = args.date or utc_today()
    http = HttpClient(min_interval=args.min_interval)

    manifest = collect(root, snapshot_date=date, http=http)

    legs = manifest["legs"]
    hf = legs.get("hf_models", {})
    print(f"snapshot {date}: {manifest['status']}  ({http.call_count} http calls)")
    for leg in mf.LEGS:
        print(f"  {leg:22} {legs.get(leg, {}).get('status', '?')}")
    if hf.get("requested"):
        print(f"  hf coverage            {hf['stored']}/{hf['requested']} stored, "
              f"{hf.get('permanent_failures', 0)} permanently unavailable")
    print(f"  -> {paths.snapshot_dir(root, date)}")

    return 0 if manifest["status"] == mf.COMPLETE else 1


if __name__ == "__main__":
    sys.exit(main())
