# Open Model Lifecycle: first Gold findings

Rebuilt from Silver on **2026-10-01 local time**. The analysis cutoff is the
latest HF snapshot, **2026-09-30 UTC**, not the machine clock. There are 34
daily Bronze snapshots from 2026-08-28 through 2026-09-30, a separate
September arXiv OAI datestamp backfill, and cached exact-ID arXiv Atom lookups.
Commands and definitions are in [`docs/gold_metric_contract.md`](docs/gold_metric_contract.md).

## 1. Executive summary

**Observed:** 18,325 HF repositories meet the early newest-leg cohort rule.
Of those, 9,990 have a mature 14-day window. **357 (3.6%)** were first observed
entering the HF top-5000 by day 14; 339 (3.4%) were observed in top on at least
three distinct days by then. These are selected HF repository outcomes, not
production deployment or a population-wide adoption rate.

**Interpretation:** the 14-day top-entry event is the clearest available
traction proxy. Exact 7/14/30-day rolling-download values are too sparsely
observed for the full cohort because most repos leave the newest-2000 sample
before entering top-5000. The primary outcome should therefore be top entry
with an explicit eligible denominator; rolling metrics remain descriptive for
the observed subset only.

## 2. Gold dataset inventory

| Dataset | Grain | Rows |
|---|---|---:|
| `model_lifecycle.parquet` | one canonical HF repository | 24,991 |
| `lifecycle_events.parquet` | repo × event type | 94,612 |
| `cohort_metrics.parquet` | first-seen ISO week × horizon | 30 |
| `summary.json` | build and DQ summary | 1 |

The complete column contract is in `gold/build.py`; `data/gold/` is derived
output and can be rebuilt. Gold contains transitions, eligibility, censoring
and horizon outcomes rather than copied daily Silver rows.

## 3. Cohort definition

The primary cohort requires first collector observation in `hf_new_models` and
source repository age at first observation of **0–4 days**. This yields 18,325
repos. Other HF repos remain in the lifecycle table but are excluded from the
primary cohort rates because many were mature before collection began.

The four-day bound reflects the newest leg's 2,000-row depth, not a claim that
all new HF repositories are represented. The cohort covers only repositories
that appeared in the collector's sampled text-generation legs.

## 4. Censoring and eligibility

- **Right censoring:** horizon N is eligible only when the latest snapshot is at
  least N+1 days after first observation. The extra day covers the allowed
  observation window: day N or N+1. No wall-clock date or interpolation enters
  the calculation.
- **Left censoring:** a repo already in top-5000 or OpenRouter on its first
  observation has a `first_observed_*` event, not an inferred entry or launch.
  Source `created_at` is separately labeled as a source timestamp.
- **Sample censoring:** newest and top are bounded lists. A missing rolling
  metric at a mature horizon stays null. Absence from top means only that the
  repo was not observed in the collected top-5000.

## 5. Current maturity

| Horizon | Eligible primary repos | Exact-window download observations | First top entry by horizon | Top on ≥3 snapshots by horizon |
|---:|---:|---:|---:|---:|
| 7d | 14,325 | 381 (2.7%) | 360 (2.5%) | 307 (2.1%) |
| 14d | 9,990 | 348 (3.5%) | 357 (3.6%) | 339 (3.4%) |
| 30d | 1,815 | 57 (3.1%) | 78 (4.3%) | 78 (4.3%) |
| 60d | 0 | 0 | 0 | 0 |
| 90d | 0 | 0 | 0 | 0 |

The oldest possible follow-up is 33 days. The 30-day rate comes only from the
earliest cohort week and is not a stable long-term estimate. No 60/90-day
success rate is reported because those denominators are zero.

## 6. Observed lifecycle transitions

Across all 24,991 observed HF repos: 12,719 first had zero downloads and later
had a positive `downloads_30d` observation; 10,105 were already positive at
first observation. There are 635 `entered_top5000` events after first HF
observation and 6,282 `first_observed_top5000` events on the first observed day.
The latter are left-censored, so they do not establish entry timing.

There are 155 first-observed OpenRouter links among HF repos with Silver
observations. Only **four** primary-cohort repos were first linked there after
their initial HF observation; this is too sparse for a strong early diffusion
outcome. Explicit arXiv references were detected at 7,386 repos; 7,348 of
those now have at least one resolved paper ID. Resolution is an enrichment
event, not a model-release stage.

## 7. Candidate definitions of sustained traction

| Outcome | Current mature sample | Measured result | Limitation and judgment |
|---|---:|---:|---|
| Enter top-5000 within 14d | 9,990 | 357 (3.6%) | Clear threshold and daily collection; **primary outcome**, still sample-relative. |
| Top on ≥3 days by 14d | 9,990 | 339 (3.4%) | Persistence check; 339/357 entrants satisfy it, so it adds little separation yet. |
| Positive `downloads_30d` after first zero | 14,325 eligible at 7d | 10,205 have a positive observation by day 7 among 11,435 initially zero | Rolling count, early observation often disappears; useful event, weak sustained outcome. |
| Downloads at day 14 | 9,990 | observed for only 348 | 96.5% missing by bounded sampling; unsuitable for an all-cohort rate. |
| OpenRouter appearance | 18,325 primary repos | only 4 later observed links | Longitudinally interesting; too sparse now. |

A cohort-relative percentile of `downloads_30d` would condition on the tiny
observed milestone subset. It is not selected as a primary outcome.

## 8. Early signals versus later outcomes

Among the **9,990** 14-day-eligible primary repos, these are descriptive
comparisons for observed top entry by day 14:

| Feature at first observation | Group size | Top entry | Rate |
|---|---:|---:|---:|
| `downloads_30d = 0` | 7,719 | 262 | 3.4% |
| `downloads_30d > 0` | 2,271 | 95 | 4.2% |
| likes = 0 | 8,999 | 186 | 2.1% |
| likes > 0 | 991 | 171 | 17.3% |
| explicit arXiv tag present initially | 3,031 | 35 | 1.2% |
| no explicit arXiv tag initially | 6,959 | 322 | 4.6% |
| `base_model` tag present initially | 6,825 | 305 | 4.5% |
| no `base_model` tag initially | 3,165 | 52 | 1.6% |

Likes show a strong association in this selected sample. This is not causal:
repositories differ in age, family, derivative type, publication, and selection
into the newest leg. The arXiv-tag comparison is especially unsuitable for a
research-effect claim; tags vary by repository type and metadata practice.
Features labeled “initially” are read from the first canonical observation;
ever-observed links are excluded from early-feature comparisons to avoid
future-information leakage.

## 9. arXiv enrichment

HF tags contain 1,194 format-canonical IDs. The official arXiv Atom API's
`id_list` lookup resolved **1,191** (99.7%); two have invalid month or zero
sequence structure, one valid-looking ID was not found, and none had a temporary
source failure. The 24 raw Atom response batches are cached. Silver now has
18,493 distinct paper rows: 17,359 from the OAI datestamp backfill plus 1,134
additional exact-ID lookups, with OAI winning where both sources returned a
paper on the same collection day.

The malformed references are `0000.00000` and `2507.00000`; the valid-looking
ID absent from the Atom response is `1710.11815`. These statuses remain visible
in `arxiv_id_resolutions` and do not remove the HF metadata links.

An HF `arxiv:` tag is an explicit metadata reference only. Neither a resolved
paper nor a positive date lag establishes official implementation, authorship,
causality, or production deployment. OAI datestamp is metadata modification
time and is never treated as paper publication time.

## 10. OpenRouter diffusion

The explicit `hugging_face_id` path produces 203 route-to-repo pairs targeting
165 HF IDs, of which 155 have HF observations. Most were present at the start
of the collection window. A first collector observation is not a launch date.
Only four primary-cohort repos show HF-first then OpenRouter-first observation.
More months are needed before this can support a useful transition analysis.

## 11. Data limitations

The 34-day panel is short; 60/90-day cohorts cannot yet mature. The bounded HF
lists select recent or already popular repos. Most primary repos leave both
samples before day-7/14/30 download milestones; null is not zero. HF downloads
are a rolling 30-day repository count, not daily usage or production adoption.
Derivatives, fine-tunes and quantizations are separate HF repos; model-family
identity remains unresolved. OpenRouter's archived ranking HTML still lacks a
verified cross-snapshot parser. arXiv links are references with uneven tagging.

## 12–13. What more history enables

At ~60 days, several weekly cohorts gain mature 30-day top-entry outcomes and
repeated-top checks. At ~90 days, 60-day cohorts appear; at ~180 days,
longer event curves and successor/family questions become plausible after
identity validation. Each horizon still requires an eligible denominator and
observed metric coverage. More history improves follow-up depth, **not** the
representativeness of the newest/top source samples.

## 14. Recommended capstone metrics

Publish eligible primary-cohort size and 14-day first top-entry rate by
first-seen week, plus the three-snapshot persistence sensitivity check.
Show initial likes and downloads as descriptive early signals with group sizes.
Publish milestone observation coverage beside any rolling-download quantile.
Keep OpenRouter and arXiv as explicit-link context until transition counts and
relationship meaning justify stronger analysis.

## 15. Recommended next engineering step

Let the daily collector run and the 30/60/90-day windows mature naturally.
Before adding another platform or a dashboard, monitor cohort eligibility,
top-snapshot completeness, missing milestone coverage, and cached arXiv lookup
status. Keep Gold as a full, versioned Silver replay while it runs comfortably
within the daily schedule. The current Gold build reports **0 DQ errors**.
