"""Descriptive repository trajectories; all core calculations use the standard library."""
import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

PRECEDENCE = {s: i for i, s in enumerate(('hf_models', 'hf_top_models', 'hf_new_models'))}


def _instant(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('fetched_at must include a UTC offset')
    return parsed.astimezone(timezone.utc)


def canonicalize(rows):
    """Resolve source precedence without changing exact repository identities."""
    groups = defaultdict(list)
    for original in rows:
        row = dict(original)
        if not row.get('hf_repo_id'):
            raise ValueError('empty repository ID')
        date.fromisoformat(row['snapshot_date'])
        _instant(row.get('fetched_at'))
        if row.get('source_leg') not in PRECEDENCE:
            raise ValueError('unknown source_leg')
        for metric in ('downloads_30d', 'likes'):
            value = row.get(metric)
            row[metric] = None if value in ('', None) else int(value)
            if row[metric] is not None and row[metric] < 0:
                raise ValueError('negative metric')
        groups[row['hf_repo_id'], row['snapshot_date']].append(row)
    result = []
    for key in sorted(groups):
        candidates = groups[key]
        # Ambiguity is rejected within every source leg, even a losing leg.
        by_leg = defaultdict(list)
        for row in candidates:
            by_leg[row['source_leg']].append(row)
        for repeats in by_leg.values():
            if any(row != repeats[0] for row in repeats[1:]):
                raise ValueError(f'ambiguous equal-precedence duplicate: {key}')
        result.append(min(candidates, key=lambda r: PRECEDENCE[r['source_leg']]))
    return result


def _relative(first, last):
    return (last - first) / first if first is not None and last is not None and first > 0 else None


def repository_features(rows):
    rows = canonicalize(rows)
    if not rows or len({r['hf_repo_id'] for r in rows}) != 1:
        raise ValueError('repository_features requires one nonempty repository')
    dates = [date.fromisoformat(r['snapshot_date']) for r in rows]
    span = (dates[-1] - dates[0]).days
    feature = dict(hf_repo_id=rows[0]['hf_repo_id'], observation_count=len(rows),
        first_date=rows[0]['snapshot_date'], last_date=rows[-1]['snapshot_date'],
        span_days=span, coverage_fraction=len(rows)/(span+1),
        max_gap_days=max(((b-a).days for a, b in zip(dates, dates[1:],
            strict=False)), default=0))
    for metric, prefix in (('downloads_30d', 'download'), ('likes', 'likes')):
        available = [r for r in rows if r[metric] is not None]
        first, last = (available[0][metric], available[-1][metric]) if available else (None, None)
        feature.update({prefix+'_observation_count': len(available), prefix+'_first': first,
            prefix+'_last': last,
            prefix+'_absolute_change': last-first if available else None,
            prefix+'_relative_change': _relative(first, last),
            prefix+'_first_date': available[0]['snapshot_date'] if available else None,
            prefix+'_last_date': available[-1]['snapshot_date'] if available else None})
    feature['eligible'] = (feature['download_observation_count'] >= 21 and span >= 28
        and feature['coverage_fraction'] >= .8 and feature['max_gap_days'] <= 3)
    return feature


def interval_changes(rows):
    groups = defaultdict(list)
    for row in canonicalize(rows):
        groups[row['hf_repo_id']].append(row)
    result = []
    for repo, observations in sorted(groups.items()):
        for a, b in zip(observations, observations[1:], strict=False):
            start, end = _instant(a.get('fetched_at')), _instant(b.get('fetched_at'))
            basis = (
                'fetched_at_utc' if start is not None
                and end is not None else 'snapshot_date_fallback'
            )
            elapsed = ((end-start).total_seconds()/86400 if basis == 'fetched_at_utc' else
                (
                    date.fromisoformat(b['snapshot_date'])
                    -date.fromisoformat(a['snapshot_date'])
                ).days)
            if elapsed <= 0:
                raise ValueError(f'nonpositive elapsed time: {repo}')
            d = (
                None if a['downloads_30d'] is None
                or b['downloads_30d'] is None else b['downloads_30d']-a['downloads_30d']
            )
            likes = None if a['likes'] is None or b['likes'] is None else b['likes']-a['likes']
            result.append(dict(hf_repo_id=repo, from_date=a['snapshot_date'],
                to_date=b['snapshot_date'],
                elapsed_days=elapsed, elapsed_time_basis=basis,
                elapsed_days_fallback=basis == 'snapshot_date_fallback',
                download_change=d,
                download_change_per_day=d/elapsed if d is not None else None,
                download_relative_change=_relative(a['downloads_30d'],
                    b['downloads_30d']), likes_change=likes))
    return result


def shape_features(rows, intervals):
    """Evidence features, not assigned lifecycle classes. Missing metric rows are not bridged."""
    available = [r for r in rows if r['downloads_30d'] is not None]
    values = [r['downloads_30d'] for r in available]
    changes = [r['download_change'] for r in intervals if r['download_change'] is not None]
    nonzero = [v for v in changes if v]
    peak = max(values) if values else None
    peak_row = next((r for r in available if r['downloads_30d'] == peak), None)
    # Weekly medians use calendar blocks anchored at first observed date.
    blocks = defaultdict(list)
    anchor = date.fromisoformat(rows[0]['snapshot_date'])
    for row in available:
        blocks[(date.fromisoformat(row['snapshot_date'])-anchor).days//7].append(
            row['downloads_30d']
        )
    medians = [statistics.median(v) for _, v in sorted(blocks.items()) if len(v) >= 4]
    week_changes = [b-a for a, b in zip(medians, medians[1:], strict=False)]
    up = sum(v > 0 for v in week_changes)/len(week_changes) if week_changes else 0
    down = sum(v < 0 for v in week_changes)/len(week_changes) if week_changes else 0
    rise = peak-values[0] if values else 0
    fall = peak-values[-1] if values else 0
    return dict(download_peak=peak,
        download_peak_date=peak_row['snapshot_date'] if peak_row else None,
        weekly_block_count=len(medians), weekly_up_fraction=up, weekly_down_fraction=down,
        adjacent_direction_reversals=sum(a*b < 0 for a, b in zip(nonzero, nonzero[1:],
            strict=False)),
        total_variation=sum(abs(v) for v in changes),
        normalized_volatility=statistics.pstdev(changes)/max(statistics.mean(values),
            1) if changes else None,
        peak_rise=rise, peak_fall=fall,
        interior_peak=bool(peak_row
            and available[0]['snapshot_date']
            < peak_row['snapshot_date']
            < available[-1]['snapshot_date']),
        weekly_medians=json.dumps(medians, separators=(',', ':')))


def write_csv(path, rows, fields=None):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def screens(features, relative=.25, absolute=10000, weekly=.75):
    counts = Counter()
    for f in features:
        delta, rel = f['download_absolute_change'], f['download_relative_change']
        if (
            rel is not None and delta >= absolute and rel >= relative
            and f['weekly_up_fraction'] >= weekly and f['weekly_block_count'] >= 4
        ):
            counts['sustained_growth'] += 1
        if (
            rel is not None and delta <= -absolute and rel <= -relative
            and f['weekly_down_fraction'] >= weekly and f['weekly_block_count'] >= 4
        ):
            counts['sustained_decline'] += 1
        if (
            f['interior_peak'] and min(f['peak_rise'], f['peak_fall']) >= absolute
            and min(f['peak_rise'], f['peak_fall']) >= relative * f['download_peak']
        ):
            counts['material_interior_peak'] += 1
    return {key: counts[key] for key in ('sustained_growth', 'sustained_decline',
        'material_interior_peak')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    with args.input.open(newline='', encoding='utf-8') as stream:
        raw = list(csv.DictReader(stream))
    rows = canonicalize(raw)
    groups = defaultdict(list)
    for row in rows:
        groups[row['hf_repo_id']].append(row)
    intervals = interval_changes(rows)
    interval_groups = defaultdict(list)
    for row in intervals:
        interval_groups[row['hf_repo_id']].append(row)
    features = []
    for repo, observations in sorted(groups.items()):
        f = repository_features(observations)
        f.update(shape_features(observations, interval_groups[repo]))
        features.append(f)
    eligible = [f for f in features if f['eligible']]
    strict = (
        [f for f in eligible if f['coverage_fraction'] >= .95 and f['span_days'] >= 42
            and f['max_gap_days'] <= 1
            and f['download_observation_count']/(f['span_days']+1) >= .95]
    )
    candidates = {
        'sustained_growth': [f for f in eligible if screens([f])['sustained_growth']],
        'sustained_decline': [f for f in eligible if screens([f])['sustained_decline']],
        'material_interior_peak': [f for f in eligible if screens([f])['material_interior_peak']],
        'volatile_rolling_count': [f for f in eligible if f['download_peak'] >= 100000
            and f['adjacent_direction_reversals'] >= 4],
    }
    scores = {'sustained_growth': lambda f: f['download_absolute_change'],
        'sustained_decline': lambda f: -f['download_absolute_change'],
        'material_interior_peak': lambda f: min(f['peak_rise'], f['peak_fall']),
        'volatile_rolling_count': lambda f: f['normalized_volatility']}
    cases, selected = [], set()
    for reason, pool in candidates.items():
        ranked = sorted(pool, key=lambda f: (-scores[reason](f), f['hf_repo_id']))
        chosen = next((f for f in ranked if f['hf_repo_id'] not in selected), None)
        if chosen:
            selected.add(chosen['hf_repo_id'])
            cases.append(dict(selection_reason=reason, **chosen))
    manifest_path = args.input.with_name('input_manifest.json')
    manifest = (
        json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    )
    dates = sorted({r['snapshot_date'] for r in rows})
    cutoff = manifest.get('existing_gold_summary', {}).get('latest_snapshot_date')
    missing = (
        {key: sum(r.get(key) in ('', None) for r in rows) for key in ('downloads_30d', 'likes',
            'fetched_at', 'created_at', 'pipeline_tag')}
    )
    summary = dict(input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        raw_rows=len(raw), canonical_rows=len(rows),
        unique_repositories=len(features),
        repeated_repositories=sum(f['observation_count'] > 1 for f in features),
        first_date=dates[0], last_date=dates[-1], snapshot_days=len(dates),
        observation_count_distribution=dict(sorted(
            Counter(f['observation_count'] for f in features).items()
        )),
        source_leg_counts=dict(sorted(Counter(r['source_leg'] for r in rows).items())),
        missing_values=missing,
        intervals=len(intervals),
        interval_time_basis=dict(Counter(r['elapsed_time_basis'] for r in intervals)),
        interval_date_gap_distribution=dict(sorted(Counter(
            (date.fromisoformat(r['to_date'])-date.fromisoformat(r['from_date'])).days
            for r in intervals
        ).items())),
        baseline_eligible=len(eligible), strict_eligible=len(strict),
        shape_sensitivity={'baseline': screens(eligible),
            'stronger_shape': screens(eligible, .5, 100000, .8),
            'strict_histories': screens(strict)},
        selected_cases=[f['hf_repo_id'] for f in cases],
        manifest_rows_match=manifest.get('rows') == len(rows),
        manifest_hash_match=(
            manifest.get('input_sha256')
            == hashlib.sha256(args.input.read_bytes()).hexdigest()
        ),
        existing_gold_cutoff=cutoff,
        overlap_rows=sum(r['snapshot_date'] <= cutoff for r in rows) if cutoff else None,
        extension_rows=sum(r['snapshot_date'] > cutoff for r in rows) if cutoff else None)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True,
        allow_nan=False)+'\n', encoding='utf-8', newline='\n')
    write_csv(args.output/'repository_features.csv', features)
    write_csv(args.output/'interval_changes.csv', intervals,
        list(intervals[0]) if intervals else ['hf_repo_id','from_date','to_date',
            'elapsed_days','download_change','download_change_per_day',
            'download_relative_change','likes_change','elapsed_time_basis',
            'elapsed_days_fallback'])
    write_csv(args.output/'cases.csv', cases, ['selection_reason']+list(features[0]))
    write_csv(args.output/'case_observations.csv',
        [r for r in rows if r['hf_repo_id'] in selected], list(rows[0]))
    report = [ '# Measured short-window trajectories', '',
        (
            f"Period: {dates[0]} to {dates[-1]} ({len(dates)} snapshot days); "
            f"{len(rows):,} canonical observations of {len(features):,} exact "
            f"repositories."
        ), '',
        (
            f"1. Only {len(eligible):,}/{len(features):,} repositories pass "
            f"the baseline exploration screen; {len(strict):,} pass stronger "
            f"coverage/span. Sampling combines a small curated leg, "
            f"top-download repositories and recently created repositories, so "
            f"these are selected histories, not an unbiased HF population."
        ),
        (
            f"2. Supported direction screens among {len(eligible):,} eligible "
            f"histories: {screens(eligible)}. Stronger shape thresholds yield "
            f"{screens(eligible, .5, 100000, .8)}; strict histories yield "
            f"{screens(strict)}. These overlapping evidence screens are not "
            f"lifecycle classes."
        ),
        (
            f"3. {sum(f['download_first'] == 0 for f in features):,} histories "
            f"have zero first available rolling count; relative endpoint "
            f"change is undefined there. Absolute changes and measured count "
            f"levels matter more than ranking percentage growth from tiny "
            f"bases."
        ),
        (
            f"4. Likes decline in "
            f"""{sum(
                f['likes_absolute_change'] is not None and f['likes_absolute_change'] < 0
                for f in features
            ):,}"""
            " histories; likes are stock counts and do not establish downloads, "
            "adoption or causes."
        ), '',
        '## Representative cases', '',
        (
            'Select at most one distinct repository per evidence screen, '
            'ranked by absolute growth/decline, minimum peak rise/fall, or '
            'normalized volatility respectively; exact ID breaks ties. No '
            'case is forced if evidence is absent. Volatility candidates '
            'require peak ≥100,000 and ≥4 adjacent nonzero sign reversals.'
        ), '']
    for f in cases:
        rel = (
            'undefined' if f['download_relative_change'] is None
            else f"{f['download_relative_change']:.1%}"
        )
        report.append(
            f"- `{f['hf_repo_id']}` ({f['selection_reason']}): observed "
            f"{f['first_date']}–{f['last_date']}, {f['observation_count']} "
            f"dates, coverage {f['coverage_fraction']:.1%}. Rolling downloads "
            f"{f['download_first']:,} ({f['download_first_date']}) → "
            f"{f['download_last']:,} ({f['download_last_date']}), change "
            f"{f['download_absolute_change']:+,} ({rel}); peak "
            f"{f['download_peak']:,} on {f['download_peak_date']}. Weekly "
            f"up/down fractions "
            f"{f['weekly_up_fraction']:.2f}/{f['weekly_down_fraction']:.2f}, "
            f"normalized interval volatility {f['normalized_volatility']:.3f}; "
            f"peak rise/fall {f['peak_rise']:,}/{f['peak_fall']:,}. Likes "
            f"{f['likes_first']} ({f['likes_first_date']}) → {f['likes_last']} "
            f"({f['likes_last_date']}), change {f['likes_absolute_change']}. "
            f"Selection is based on measured levels and absolute changes, not "
            f"percentage-growth rank; even large rolling counts do not "
            f"establish broad human popularity, particularly for testing or "
            f"automated-use repositories. These are measured facts; releases, "
            f"promotion, automation and source revisions are untested causal "
            f"hypotheses."
        )
    report += ['', '## Methods and limits', '',
        (
            'Baseline: ≥21 non-null download observations, span ≥28 days, '
            'date coverage ≥0.8, maximum date spacing ≤3 days. Strict: span '
            '≥42, date and download coverage ≥0.95, spacing ≤1 day. '
            'Direction: ≥4 weekly blocks with ≥4 measured values each, ≥75% '
            'successive block median changes in the stated direction, '
            'endpoint absolute change ≥10,000 and relative change ≥25%. '
            'Stronger shape: ≥80%, ≥100,000 and ≥50%. Interior peak: measured '
            'maximum strictly inside metric endpoints, both rise and fall '
            '≥10,000 and ≥25% of peak (stronger: 100,000 and 50%). Flat '
            'weekly comparisons remain in the denominator. Thresholds are '
            'pragmatic and ambiguous near boundaries.'
        ),
        (
            'Weekly blocks are anchored at each repository’s first observed '
            'date; sparse blocks are omitted, so block comparisons need not '
            'be consecutive weeks. Total variation uses only adjacent valid '
            'endpoints; nonzero sign reversal counts can span flat or missing '
            'intervals and are screening evidence, not proof of a sustained '
            'reversal. Interior peak rise/fall is endpoint evidence, not '
            'proof of a unique spike or persistent reversal. No interpolation '
            'or extrapolation is performed.'
        ),
        (
            '47 days of overlapping rolling counts support descriptive '
            'short-window patterns, not established lifecycle classes or '
            'predictive reliability. Rolling 30-day counts overlap; rates are '
            'changes in that rolling metric, never actual daily downloads. No '
            'cumulative downloads can be derived. Counts reflect '
            'format-dependent file requests, not unique users. Exact '
            'repository IDs remain separate. Creation dates do not recover '
            'unobserved early histories. Endpoint changes may use shorter '
            'metric-specific periods when endpoints are missing.'
        ),
        (
            'Next research: prospectively defined early prediction with '
            'time-separated validation; persistence/survival with censoring '
            'and longer follow-up; category comparisons with explicit '
            'sampling and confounding controls. None is implemented here.'
        ), '',
        (
            'Validation: this implementation was executed on the supplied '
            'extract and outputs inspected. Frozen independent acceptance '
            'tests and parent plotting validation remain external; no passes '
            'are claimed.'
        )]
    (args.output/'REPORT.md').write_text('\n'.join(report)+'\n', encoding='utf-8', newline='\n')
    audit = ['# Input and computation audit',
        (
                 f"SHA-256: `{summary['input_sha256']}`. Manifest hash match: "
                 f"{summary['manifest_hash_match']}; row count match: "
                 f"{summary['manifest_rows_match']}."
        ),
        (
                 f"Raw/canonical rows: {len(raw):,}/{len(rows):,}; collapsed or "
                 f"precedence-discarded rows: {len(raw)-len(rows):,}. "
                 f"Equal-precedence ambiguous duplicates raise an error. Exact "
                 f"case-preserved identities only."
        ),
        (
                 f"Unique/repeated repositories: "
                 f"{len(features):,}/{summary['repeated_repositories']:,}. "
                 f"Observation-count distribution: "
                 f"{summary['observation_count_distribution']}."
        ),
        (
                 f"Missing values: {missing}. Source legs: "
                 f"{summary['source_leg_counts']}."
        ),
        (
                 f"Intervals: {len(intervals):,}; time bases: "
                 f"{summary['interval_time_basis']}; date-spacing distribution: "
                 f"{summary['interval_date_gap_distribution']}. Nonpositive elapsed "
                 f"UTC time raises an error. Missing metrics invalidate only their "
                 f"adjacent changes; no bridging, zero filling or infinite relative "
                 f"rates."
        ),
        (
                 f"Existing Silver DQ coverage: "
                 f"{manifest.get('existing_silver_dq', {}).get('coverage')}; "
                 f"metadata row counters: "
                 f"{manifest.get('existing_silver_dq', {}).get('rows')}. Existing "
                 f"Gold cutoff: {cutoff}; research overlap rows: "
                 f"{summary['overlap_rows']}; extension rows: "
                 f"{summary['extension_rows']}."
        ),
        (
            'The supplied research context independently checked all 237930 '
            'canonical Silver overlap rows through October 1 against this '
            'raw-derived extract. This is external evidence, not a check '
            'rerun here. The Silver DQ hf_observations counter includes a '
            'different row representation; do not equate it with the '
            'canonical research overlap. Gold primary cohorts and milestone '
            'denominators differ from these descriptive screens.'
        ),
        (
                 f"Manifest parser errors: {manifest.get('parser_errors')}; "
                 f"protected files unchanged (manifest assertion): "
                 f"{manifest.get('protected_files_unchanged')}. Source hashes: "
                 f"{len(manifest.get('source_hashes', {}))}; parser SHA-256: "
                 f"{manifest.get('parser_sha256')}."
        ),
        (
            'Selection bias: leg availability varies by date, top-ranked '
            'sampling is outcome-dependent, new-model histories are short and '
            'exits can be sampling exits. Within-span coverage can be 100% '
            'for a singleton; it does not imply long follow-up. Eligibility '
            'uses the full retrospective history and must not be used as an '
            'early predictive feature.'
        ),
        (
                 f"Eligibility denominator: all {len(features):,} exact "
                 f"repositories; baseline {len(eligible):,}, strict "
                 f"{len(strict):,}. Shape sensitivity: "
                 f"{summary['shape_sensitivity']}."
        ),
        (
            'Reproduction is deterministic: sorted IDs/dates, fixed '
            'thresholds and tie breaking, no random seed or wall-clock '
            'metadata. Independent testing and plot execution are supplied by '
            'the parent; no acceptance result is asserted.'
        )]
    (args.output/'AUDIT.md').write_text('\n\n'.join(audit)+'\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
