# Public research contract

Investigate measured popularity trajectories in existing HF historical observations.
This is descriptive, retrospective research, not prediction or causal inference.
Keep exact, case-preserved HF repository IDs separate; no family aggregation.
Use the existing Silver parser and precedence: hf_models, hf_top_models, hf_new_models.
Never modify source data, Silver/Gold, ingestion, identity rules, or infrastructure.

## Public interface

`analysis.py` uses the Python standard library and exports:

- `canonicalize(rows) -> list[dict]`: unique repo/snapshot-date rows in chronological
  order within exact repo ID; apply the above source precedence; reject ambiguous
  duplicates at equal precedence (identical repeats may collapse).
- `repository_features(rows) -> dict`: one exact repository's observations, any
  input order. Canonicalize first. Fields: `hf_repo_id`, `observation_count`,
  `first_date`, `last_date`, `span_days`, `coverage_fraction`, `max_gap_days`,
  `download_observation_count`, `download_first`, `download_last`,
  `download_absolute_change`, `download_relative_change`, `likes_first`,
  `likes_last`, `likes_absolute_change`, `eligible`.
- `interval_changes(rows) -> list[dict]`: adjacent canonical observations within
  exact repository, ordered by snapshot_date; fields `hf_repo_id`, `from_date`,
  `to_date`, `elapsed_days`, `download_change`, `download_change_per_day`,
  `download_relative_change`, `likes_change`. Each metric uses both interval
  endpoints; a missing metric yields null for its change. Do not bridge missing
  metric rows. elapsed_days uses actual fetched_at UTC instants when both exist;
  otherwise date spacing, with this fallback explicitly marked. Nonpositive
  elapsed time is an error. Per-day change is the rate of change of the rolling
  metric, never actual daily downloads.
- CLI: `python analysis.py --input INPUT.csv --output OUTPUT_DIRECTORY`.
  Produce `summary.json`, `repository_features.csv`, `interval_changes.csv`,
  `cases.csv`, `case_observations.csv`, `REPORT.md`, `AUDIT.md`.

Input fields: hf_repo_id, snapshot_date, fetched_at, source_leg, downloads_30d,
likes, created_at, pipeline_tag. CSV empty metric fields become None; accept
integer/None metrics for direct calls. Downloads mean rolling 30-day counts.
No cumulative downloads or actual daily downloads can be derived here. Likes
are observed stock counts, can decline, and do not prove downloads or adoption.

Download endpoint fields use first and last NON-NULL download observation; likes
likewise use their own first and last non-null observations. No available values
means null endpoints and changes; one value means absolute change 0. Expose
metric endpoint dates as additional fields to avoid implying missing endpoints
were measured. Relative change = (last-first)/first for first>0; None at zero
baseline. Absolute change remains valid at zero. No infinite rates or zero fill.

Coverage fraction = number of distinct observed dates / (span_days+1).
max_gap_days = maximum spacing of observed snapshot dates (0 for a singleton).
Baseline eligibility for shape exploration: at least 21 non-null download
observations, span >=28 days, coverage >=0.8, max_gap_days <=3. This is a pragmatic
research screen, not proof of reliability or representativeness. Report criteria,
denominators and sensitivity to stricter coverage and longer span. Do not use
eligible histories as an unbiased sample of all HF repositories.

## Required research behavior

Audit unique/repeated repositories, observation-count distribution, dates,
intervals, missing values and selection bias. Reconcile the stale existing
Silver/Gold cutoff with the isolated raw-derived research extract. Identify
interpretable measured growth, decline, volatility, reversals and spike cases
only where supported; hypotheses in the user brief are not mandatory classes.
Choose cases deterministically and explain the selection and measured values.
Document thresholds, their sensitivity and ambiguity. Do not interpolate missing
observations or extrapolate beyond observed periods. Charts must use the same
observations and case IDs as tables and prose, with gaps visible and accurately
labeled axes. No future-data leakage in any suggestion of predictive validity.
Separate cause hypotheses from measured observations.

Produce 3-5 evidence-backed findings if supported, representative cases with
periods/metrics/changes, limitations, and next directions (early prediction,
persistence/survival, category comparisons) without implementing them.
`plot.py` may use existing bundled matplotlib; core calculations remain stdlib.
Reproduction must be deterministic from pinned inputs. Report validation and
actual contributions from independent testing/review honestly; do not invent
passes. Record observed check outcomes and their limitations.
