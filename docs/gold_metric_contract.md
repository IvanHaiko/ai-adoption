# Gold metric contract (logic version 1)

`python -m gold` fully rebuilds `data/gold/` from the current Silver Parquet
tables. There is no wall-clock dependency: the latest `hf_repositories`
`snapshot_date` is the analysis cutoff. A later daily Silver replay matures old
cohorts automatically. Outputs are deterministic and rebuildable, so a full
replay is simpler than incremental Gold state at the present dataset size.

## Grains and source meaning

- `model_lifecycle.parquet`: one row per canonical **HF repository ID**.
  This is not one row per underlying model family.
- `lifecycle_events.parquet`: one row per HF repo and observed/source event type.
  `observed_vs_source_timestamp` separates collector dates from source-provided
  creation dates. `first_observed_openrouter` is a first catalogue observation,
  never an inferred launch date. `arxiv_reference_resolved` is a metadata lookup
  event, not a research-to-model event.
- `cohort_metrics.parquet`: one row per first-seen ISO week and horizon
  (7, 14, 30, 60, 90 days), including cohort size, eligible denominator,
  milestone coverage, top-entry and repeated-top counts.
- `summary.json`: build cutoff, cohort maturity counts, event counts and DQ.

## Primary cohort and censoring

The primary cohort contains a repo if its earliest collector observation has
`first_seen_source = hf_new_models` and its source `created_at` date is 0–4 days
before `first_seen_date`. This excludes much older left-censored repositories.
The age threshold reflects the collector's 2,000-newest sampling depth; it does
not make the cohort representative of all HF models. Repos first seen in top or
OpenRouter detail remain in `model_lifecycle` but outside primary cohort rates.

For horizon N, `observed_followup_days = latest_snapshot_date - first_seen_date`.
`eligible_Nd` is true only if follow-up is at least **N+1** days, allowing the
full target-day tolerance window. An eligible repo may have no download/like
observation in that window; its milestone metrics stay null and
`milestone_observed_Nd` is false. The milestone is the **first actual** canonical
HF observation on day N or N+1. There is no interpolation or zero fill.

Top-entry outcomes use the daily top-5000 sample, whose complete snapshots are
checked separately in Bronze. `top_entry_by_Nd` is true only when first top
observation occurs after first repo observation and by day N. A repo already in
top on its first observed day has `first_observed_top5000` and
`left_censored_top = true`; it is not counted as a measured entry. Within an
eligible cohort, a missing top observation by day N means **not observed in
top-5000**, not that it had no downloads or was never hosted.

`sustained_top_by_Nd` additionally requires top membership on at least three
distinct daily snapshots after first observation and by day N. This is an
alternative observed persistence measure, not a usage or adoption score.
OpenRouter links and arXiv references are contextual metadata. Their first
observation dates cannot be interpreted as platform launch or research release.

## Primary outcome and alternatives

For the first capstone analysis use **entered HF top-5000 within 14 days** among
eligible primary-cohort repos. Report numerator, eligible denominator and
collection cutoff together. The metric has a clear sampling threshold and can
be recomputed as history grows. It is a public traction proxy, not production
adoption. Use repeated top presence as a sensitivity check. Avoid a general
30-day downloads milestone rate: most newest repos disappear from both bounded
HF list legs before that milestone, making its observed subset highly selected.

Other candidates remain visible in Gold: first nonzero rolling 30-day downloads,
top entry at 7/30/60/90 days, repeated top presence, milestone download/like
values when observed, and explicit OpenRouter catalogue presence. Their
eligibility and observation coverage must be reported before comparing groups.

## Quality rules and future processing

The build reports or fails on duplicate HF lifecycle IDs, duplicate event types,
negative source-to-observation ages or observed durations, inconsistent maturity
flags, metrics populated for immature windows, and inconsistent route counts.
Malformed Silver inputs are errors. More history increases follow-up depth; it
does not remove newest/top sampling bias. Incremental Gold processing becomes
useful only if full Silver replay and Gold rebuild grow too slow for the daily
schedule, after benchmark evidence rather than in advance.
