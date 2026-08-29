"""Audit the Bronze layer on disk and say what is wrong with it.

Three failures this exists to catch, in rising order of nastiness.

A day is *missing* — visible by counting, and unrecoverable, so the only useful
response is to notice fast.

A day is *corrupt* — present, complete, and not what was downloaded. Git and
gzip both make this unlikely; a manifest that records the SHA-256 of the bytes
as received makes it detectable, which is the only reason to have written the
hash down.

A day is *thin* — present, complete, internally consistent, and quietly missing
most of its rows because HuggingFace started rate-limiting halfway through.
Nothing inside the collector can see this: every individual response was a
legitimate answer. Only the comparison against yesterday shows it. This is the
Bronze-layer form of the project's rule that a cross-source join publishes its
coverage and fails the build when that coverage regresses.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

from . import manifest as mf
from . import paths
from .storage import read_gzip, read_json, sha256

ERROR = "ERROR"
WARN = "WARN"

# Chosen, not measured: there is one day of history so far. Small day-over-day
# drops are ordinary, because repositories get gated or deleted upstream, and
# 151 of 152 is the only real observation available. Revisit both numbers once
# there are a few weeks of days to look at.
COVERAGE_WARN_DROP = 0.05
COVERAGE_ERROR_DROP = 0.20

# One day of slack: the 06:20 UTC run has not happened yet when a European
# morning starts, so "yesterday is the latest" is the healthy steady state.
MAX_LAG_DAYS = 1

# Acknowledged permanent losses, at the repository root.
KNOWN_GAPS = "known_gaps.txt"


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    message: str
    day: str | None = None

    def __str__(self) -> str:
        where = self.day or "-"
        return f"{self.level:5} {where:10} {self.check:12} {self.message}"


def _is_date(name: str) -> bool:
    try:
        dt.date.fromisoformat(name)
    except ValueError:
        return False
    return True


def snapshot_days(root: Path) -> list[str]:
    raw = root / paths.RAW
    if not raw.is_dir():
        return []
    return sorted(p.name for p in raw.iterdir() if p.is_dir() and _is_date(p.name))


def read_known_gaps(root: Path) -> dict[str, str]:
    """Days whose absence has been acknowledged, mapped to the stated reason.

    Acknowledging is not repairing: the data is gone either way. The point is
    that a permanent loss would otherwise leave the audit red forever, and an
    alert that is always red stops being read - at which cost it no longer
    catches the next gap, which is the only thing it is for.
    """
    path = root / KNOWN_GAPS
    if not path.exists():
        return {}
    gaps: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        date, _, reason = line.partition(" ")
        if _is_date(date):
            gaps[date] = reason.strip() or "no reason given"
    return gaps


def check_gaps(days: list[str], known: dict[str, str] | None = None) -> list[Finding]:
    """Every date between the first and the last snapshot must be present.

    Measured between first and last, never up to today: the distance from the
    last snapshot to today is lag, reported separately. Counting it here would
    report the same absent days twice under two different names.
    """
    if len(days) < 2:
        return []
    known = known or {}
    first, last = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    present = {dt.date.fromisoformat(d) for d in days}
    missing = [
        first + dt.timedelta(days=offset)
        for offset in range((last - first).days + 1)
        if first + dt.timedelta(days=offset) not in present
    ]

    findings = []
    for day in missing:
        reason = known.get(str(day))
        if reason:
            findings.append(Finding(WARN, "gap", f"acknowledged: {reason}", str(day)))
        else:
            findings.append(
                Finding(ERROR, "gap", "no snapshot, and it cannot be collected later", str(day))
            )
    return findings


def check_staleness(days: list[str], today: dt.date, max_lag: int) -> list[Finding]:
    if not days:
        return [Finding(ERROR, "staleness", "no snapshots at all")]
    lag = (today - dt.date.fromisoformat(days[-1])).days
    if lag <= max_lag:
        return []
    return [
        Finding(
            ERROR,
            "staleness",
            f"latest snapshot is {lag} days behind {today}; "
            "check the collect workflow is still enabled",
            days[-1],
        )
    ]


def _check_leg(root: Path, day: str, leg: str, record: dict) -> list[Finding]:
    """The stored bytes must be the bytes the manifest says were received."""
    if record.get("status") != mf.OK:
        return []  # already reported by the day-level status check

    expected = record.get("sha256_raw")
    if not expected:
        return [Finding(ERROR, "integrity", f"{leg}: manifest records no sha256_raw", day)]

    path = paths.leg_path(root, day, leg)
    if not path.exists():
        return [Finding(ERROR, "integrity", f"{leg}: {path.name} is missing", day)]

    on_disk = path.stat().st_size
    if record.get("bytes_stored") not in (None, on_disk):
        return [
            Finding(
                ERROR,
                "integrity",
                f"{leg}: {on_disk} bytes on disk, manifest says {record['bytes_stored']}",
                day,
            )
        ]

    try:
        raw = read_gzip(path)
    except OSError as exc:
        return [Finding(ERROR, "integrity", f"{leg}: cannot decompress ({exc})", day)]

    actual = sha256(raw)
    if actual != expected:
        return [
            Finding(
                ERROR,
                "integrity",
                f"{leg}: sha256 {actual[:12]} does not match manifest {expected[:12]}",
                day,
            )
        ]
    return []


def check_day(root: Path, day: str) -> list[Finding]:
    """Completeness and integrity of a single day."""
    path = paths.manifest_path(root, day)
    if not path.exists():
        return [Finding(ERROR, "manifest", "no _manifest.json", day)]
    try:
        manifest = read_json(path)
    except ValueError as exc:
        return [Finding(ERROR, "manifest", f"unreadable: {exc}", day)]

    findings: list[Finding] = []
    if manifest.get("status") != mf.COMPLETE:
        findings.append(
            Finding(ERROR, "incomplete", f"status is {manifest.get('status')!r}", day)
        )
    for leg in mf.LEGS:
        findings.extend(_check_leg(root, day, leg, manifest.get("legs", {}).get(leg, {})))
    return findings


def _compare_coverage(prev_day: str, prev: dict, day: str, cur: dict) -> list[Finding]:
    findings: list[Finding] = []

    drop = prev["ratio"] - cur["ratio"]
    level = None
    if drop >= COVERAGE_ERROR_DROP:
        level = ERROR
    elif drop >= COVERAGE_WARN_DROP:
        level = WARN
    if level:
        findings.append(
            Finding(
                level,
                "coverage",
                f"hf coverage {cur['stored']}/{cur['requested']} ({cur['ratio']:.0%}) "
                f"against {prev['ratio']:.0%} on {prev_day} - "
                "a day can be complete and still be thin",
                day,
            )
        )

    # A shrinking catalogue is a different failure from a failing fetch: the
    # rows were never asked for, rather than asked for and lost.
    if cur["requested"] < prev["requested"] * (1 - COVERAGE_ERROR_DROP):
        findings.append(
            Finding(
                WARN,
                "catalogue",
                f"{cur['requested']} repositories requested against "
                f"{prev['requested']} on {prev_day}",
                day,
            )
        )
    return findings


def check_coverage(root: Path, days: list[str]) -> list[Finding]:
    """Day-over-day collapse in how much of the catalogue was actually captured."""
    findings: list[Finding] = []
    previous: tuple[str, dict] | None = None

    for day in days:
        path = paths.manifest_path(root, day)
        if not path.exists():
            continue
        try:
            hf = read_json(path).get("legs", {}).get("hf_models", {})
        except ValueError:
            continue
        requested, stored = hf.get("requested"), hf.get("stored")
        if not requested or stored is None:
            continue
        current = {"requested": requested, "stored": stored, "ratio": stored / requested}

        if previous is not None:
            findings.extend(_compare_coverage(previous[0], previous[1], day, current))
        previous = (day, current)

    return findings


def check_ranking_rows(root: Path, days: list[str]) -> list[Finding]:
    """The ranking leg can succeed and still bring back a fraction of the rows.

    Same failure as a thin `hf_models` day, different leg: every page returned
    200, the leg is `ok`, and the Hub simply stopped paginating early. Only
    yesterday's row count shows it.
    """
    findings: list[Finding] = []

    for name in mf.LIST_LEGS:
        previous: tuple[str, int] | None = None
        for day in days:
            path = paths.manifest_path(root, day)
            if not path.exists():
                continue
            try:
                leg = read_json(path).get("legs", {}).get(name, {})
            except ValueError:
                continue
            rows = leg.get("rows")
            if leg.get("status") != mf.OK or not rows:
                continue

            if previous is not None:
                prev_day, prev_rows = previous
                drop = (prev_rows - rows) / prev_rows
                if drop >= COVERAGE_ERROR_DROP:
                    findings.append(
                        Finding(
                            ERROR,
                            "ranking",
                            f"{name}: {rows} rows against {prev_rows} on {prev_day} "
                            f"({drop:.0%} fewer) - the leg is ok and still thin",
                            day,
                        )
                    )
            previous = (day, rows)

    return findings


def audit(
    root: Path, today: dt.date | None = None, max_lag: int = MAX_LAG_DAYS
) -> list[Finding]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    days = snapshot_days(root)

    findings = check_staleness(days, today, max_lag) + check_gaps(days, read_known_gaps(root))
    for day in days:
        findings.extend(check_day(root, day))
    findings.extend(check_coverage(root, days))
    findings.extend(check_ranking_rows(root, days))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collector.audit", description="Audit Bronze.")
    parser.add_argument("--root", default=None, help="repository root to audit")
    parser.add_argument("--max-lag-days", type=int, default=MAX_LAG_DAYS)
    parser.add_argument(
        "--warn-is-failure", action="store_true", help="exit non-zero on warnings too"
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else paths.repo_root()
    findings = audit(root, max_lag=args.max_lag_days)
    days = snapshot_days(root)

    span = f"{days[0]}..{days[-1]}" if days else "none"
    print(f"audited {len(days)} snapshot day(s) ({span}) under {root / paths.RAW}")
    for finding in findings:
        print(f"  {finding}")
    if not findings:
        print("  ok")

    errors = sum(1 for f in findings if f.level == ERROR)
    warnings = len(findings) - errors
    print(f"{errors} error(s), {warnings} warning(s)")

    return 1 if errors or (warnings and args.warn_is_failure) else 0


if __name__ == "__main__":
    sys.exit(main())
