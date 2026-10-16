"""Publication figures from the analysis tables; no source-data recalculation."""
import argparse
import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, StrMethodFormatter


def read_table(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def save(fig, directory, name):
    # Fixed metadata and SVG salt make repeated saves byte-deterministic in the
    # same pinned runtime (font and matplotlib versions also affect rendering).
    fig.savefig(directory / (name + '.png'), dpi=200, metadata={'Software': 'trajectory-research'})
    fig.savefig(directory / (name + '.svg'), metadata={'Date': None,
        'Creator': 'trajectory-research'})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    plt.rcParams.update({'svg.hashsalt': 'hf-short-window-trajectories-v1',
        'font.family': 'DejaVu Sans', 'font.size': 9,
        'axes.spines.top': False, 'axes.spines.right': False,
        'axes.titleweight': 'bold', 'axes.grid': True,
        'grid.alpha': .2, 'figure.facecolor': 'white'})
    cases = read_table(args.results / 'cases.csv')
    observations = defaultdict(dict)
    for row in read_table(args.results / 'case_observations.csv'):
        observations[row['hf_repo_id']][date.fromisoformat(row['snapshot_date'])] = row
    output = args.results / 'plots'
    output.mkdir(parents=True, exist_ok=True)
    count = max(len(cases), 1)
    fig, axes = plt.subplots(count, 2, figsize=(12, 2.8 * count + 1), squeeze=False,
        constrained_layout=True)
    for index, case in enumerate(cases):
        first, last = date.fromisoformat(case['first_date']), date.fromisoformat(case['last_date'])
        dates = [first + timedelta(days=n) for n in range((last-first).days+1)]
        history = observations[case['hf_repo_id']]
        for column, (metric, label, color) in enumerate([
            ('downloads_30d', 'Downloads in preceding 30 days (count)', '#146a91'),
            ('likes', 'Observed likes (stock count)', '#9b4b19')]):
            ax = axes[index, column]
            # Full daily grid with NaNs exposes both absent rows and null metrics.
            values = [float(history.get(day, {}).get(metric) or 'nan') for day in dates]
            ax.plot(dates, values, color=color, linewidth=1.5, marker='o', markersize=2.4)
            ax.set_title(case['hf_repo_id'] + '\n' + case['selection_reason'].replace('_',
                ' '), fontsize=9)
            ax.set_ylabel(label)
            ax.set_xlabel('Snapshot date (UTC calendar date)')
            if metric == 'likes':
                ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
            locator = mdates.AutoDateLocator(minticks=3, maxticks=5)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            ax.tick_params(axis='both', labelsize=8)
    if not cases:
        for ax in axes[0]:
            ax.text(.5, .5, 'No cases met the evidence screens', ha='center',
                transform=ax.transAxes)
    fig.suptitle((
        'Measured repository trajectories\nIndependent panel scales; gaps '
        'are unobserved or missing values. Rolling counts are not daily '
        'downloads.'
    ), fontsize=12)
    save(fig, output, 'representative_cases')

    features = read_table(args.results / 'repository_features.csv')
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.9), constrained_layout=True)
    axes[0].hist([int(r['observation_count']) for r in features],
        bins=[.5+n for n in range(max(int(r['observation_count']) for r in features)+1)],
        color='#146a91')
    axes[0].set_xlabel('Observed snapshot dates per repository')
    axes[1].hist([float(r['coverage_fraction']) for r in features],
        bins=[n/20 for n in range(21)], color='#146a91')
    axes[1].set_xlabel('Observed dates / (within-history span + 1)')
    axes[1].axvline(.8, color='#9b4b19', linestyle='--', label='Baseline coverage screen')
    axes[1].legend(fontsize=7)
    axes[2].hist([int(r['span_days']) for r in features],
        bins=[-.5+n for n in range(max(int(r['span_days']) for r in features)+2)], color='#146a91')
    axes[2].set_xlabel('First-to-last observed span (days)')
    axes[2].axvline(28, color='#9b4b19', linestyle='--')
    for ax in axes:
        ax.set_ylabel('Exact repositories (count)')
        ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    fig.suptitle((
        f'Coverage of {len(features):,} sampled exact '
        f'repositories\nWithin-history coverage does not measure '
        f'population coverage; a singleton has coverage 1.'
    ), fontsize=12)
    save(fig, output, 'coverage')


if __name__ == '__main__':
    main()
