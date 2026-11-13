"""Render the README charts from Gold.

    python -m gold && python -m scripts.make_charts

Reads `data/gold/model_lifecycle.parquet` and `data/gold/cohort_metrics.parquet`,
writes a light and a dark PNG per chart to `docs/img/`, and `docs/img/charts.json`
with every plotted number and the Gold cutoff it came from - so a figure quoted
in the README can be traced to a build rather than to memory.

The arithmetic lives in plain functions with no plotting import, so it is tested
without matplotlib (`tests/test_charts.py`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "gold"
OUT = ROOT / "docs" / "img"

CURVE_HORIZON = 60
WEEK_HORIZON = 14

# Reference palette, categorical slot 1, stepped per surface.
THEMES = {
    "light": {"surface": "#ffffff", "ink": "#0b0b0b", "muted": "#52514e",
              "grid": "#e4e3df", "series": "#2a78d6"},
    "dark": {"surface": "#0d1117", "ink": "#f0efec", "muted": "#c3c2b7",
             "grid": "#30363d", "series": "#3987e5"},
}


def entry_curve(lifecycle: list[dict], horizon: int) -> dict:
    """Cumulative share of one fixed cohort in the top-5000 by day N, N = 0..horizon.

    One cohort for the whole curve: only primary-cohort repositories already
    observed for `horizon` days. Mixing cohorts per day would let a later,
    smaller denominator bend the curve and read as a change over time.
    """
    cohort = [r for r in lifecycle if r["primary_cohort"] and r[f"eligible_{horizon}d"]]
    days = [r["days_to_top5000"] for r in cohort if r["days_to_top5000"] is not None]
    share = [
        sum(1 for d in days if d <= n) / len(cohort) if cohort else 0.0
        for n in range(horizon + 1)
    ]
    first_seen = sorted(r["first_seen_date"] for r in cohort)
    return {
        "horizon_days": horizon,
        "cohort_size": len(cohort),
        "first_seen_from": first_seen[0] if first_seen else None,
        "first_seen_until": first_seen[-1] if first_seen else None,
        "share_by_day": share,
    }


def weekly_rates(metrics: list[dict], horizon: int) -> list[dict]:
    """Top-entry rate per first-seen week, for weeks whose whole cohort is eligible.

    A week still partly inside its follow-up window would show the rate of its
    earliest days only, so it is left out rather than drawn short.
    """
    rows = [
        r for r in metrics
        if r["horizon_days"] == horizon
        and r["cohort_size"]
        and r["eligible_count"] == r["cohort_size"]
    ]
    return [
        {
            "week": r["first_seen_week"],
            "eligible": r["eligible_count"],
            "entered": r["top_entry_count"],
            "rate": r["top_entry_count"] / r["eligible_count"],
        }
        for r in sorted(rows, key=lambda r: r["first_seen_week"])
    ]


def _style(ax, theme: dict) -> None:
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme["grid"])
    ax.tick_params(colors=theme["muted"], length=0, labelsize=10)
    ax.grid(axis="y", color=theme["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def render_curve(curve: dict, theme: dict, path: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    share = [s * 100 for s in curve["share_by_day"]]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150, facecolor=theme["surface"])
    _style(ax, theme)
    ax.step(range(len(share)), share, where="post", color=theme["series"], linewidth=2)
    for day in (14, curve["horizon_days"]):
        ax.plot(day, share[day], "o", markersize=7, color=theme["series"],
                markeredgecolor=theme["surface"], markeredgewidth=2, zorder=3)
        ax.annotate(f"day {day}: {share[day]:.1f}%", (day, share[day]),
                    xytext=(-8, 10), textcoords="offset points", ha="right",
                    color=theme["ink"], fontsize=10)
    ax.set_xlim(0, curve["horizon_days"] + 1)
    ax.set_ylim(0, max(share) * 1.25)
    ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_xlabel("days since the collector first saw the repository", color=theme["muted"])
    reached = curve["share_by_day"][14] / curve["share_by_day"][-1] if share[-1] else 0.0
    fig.suptitle(f"{reached:.0%} of new repos that reach the HF top-5000 by day "
                 f"{curve['horizon_days']} are there by day 14",
                 x=0.02, ha="left", color=theme["ink"], fontsize=12, fontweight="bold")
    ax.set_title(
        f"Share in the top-5000 by day N. "
        f"One cohort: {curve['cohort_size']:,} text-generation repos\n"
        f"first seen {curve['first_seen_from']}..{curve['first_seen_until']} at 0-4 days old",
        loc="left", color=theme["muted"], fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(path, facecolor=theme["surface"])
    plt.close(fig)


def render_weeks(weeks: list[dict], pooled: float, theme: dict, path: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    labels = [w["week"].split("-")[1] for w in weeks]
    rates = [w["rate"] * 100 for w in weeks]
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150, facecolor=theme["surface"])
    _style(ax, theme)
    ax.bar(labels, rates, width=0.7, color=theme["series"], edgecolor=theme["surface"], linewidth=2)
    ax.axhline(pooled * 100, color=theme["muted"], linewidth=1, linestyle=(0, (4, 3)))
    for i in (rates.index(min(rates)), rates.index(max(rates))):
        ax.annotate(f"{rates[i]:.1f}%", (i, rates[i]), xytext=(0, 3), textcoords="offset points",
                    ha="center", color=theme["ink"], fontsize=9)
    ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_ylim(0, max(rates) * 1.3)
    ax.set_xlabel("ISO week the repository was first seen (2026)", color=theme["muted"])
    fig.suptitle(f"Top-5000 entry within {WEEK_HORIZON} days swings week to week",
                 x=0.02, ha="left", color=theme["ink"], fontsize=12, fontweight="bold")
    ax.set_title(f"Weekly cohorts with a complete {WEEK_HORIZON}-day follow-up; "
                 f"{sum(w['eligible'] for w in weeks):,} repos in all; "
                 f"dashed line: all weeks pooled, {pooled:.1%}",
                 loc="left", color=theme["muted"], fontsize=9)
    fig.tight_layout()
    fig.savefig(path, facecolor=theme["surface"])
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.make_charts", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    lifecycle = pq.read_table(args.gold / "model_lifecycle.parquet").to_pylist()
    metrics = pq.read_table(args.gold / "cohort_metrics.parquet").to_pylist()
    summary = json.loads((args.gold / "summary.json").read_text(encoding="utf-8"))

    curve = entry_curve(lifecycle, CURVE_HORIZON)
    weeks = weekly_rates(metrics, WEEK_HORIZON)
    pooled = sum(w["entered"] for w in weeks) / sum(w["eligible"] for w in weeks)

    args.out.mkdir(parents=True, exist_ok=True)
    for mode, theme in THEMES.items():
        suffix = "" if mode == "light" else "-dark"
        render_curve(curve, theme, args.out / f"top5000_entry_curve{suffix}.png")
        render_weeks(weeks, pooled, theme, args.out / f"top5000_entry_by_week{suffix}.png")

    record = {
        "gold_latest_snapshot_date": summary["latest_snapshot_date"],
        "gold_first_snapshot_date": summary["first_snapshot_date"],
        "curve": {**curve, "share_by_day": [round(s, 5) for s in curve["share_by_day"]]},
        "weekly": [{**w, "rate": round(w["rate"], 5)} for w in weeks],
        "weekly_pooled_rate": round(pooled, 5),
    }
    (args.out / "charts.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"charts from Gold through {summary['latest_snapshot_date']} -> {args.out}")
    print(f"  curve: {curve['cohort_size']} repos, day 14 {curve['share_by_day'][14]:.2%}, "
          f"day {CURVE_HORIZON} {curve['share_by_day'][-1]:.2%}")
    print(f"  weekly {WEEK_HORIZON}d: {len(weeks)} weeks, pooled {pooled:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
