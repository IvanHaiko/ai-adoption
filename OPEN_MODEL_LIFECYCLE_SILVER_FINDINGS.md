# Open Model Lifecycle: first Silver findings

Measured by a full local replay on **2026-10-01** from the 34 Bronze days dated
2026-08-28 through 2026-09-30. These are collector observations, not population
estimates for the open-model ecosystem. Generated Parquet and machine-readable DQ
live in `data/silver/` and can be reproduced with `python -m silver`.

## 1. Silver inventory

| Dataset | Rows | Unique entities / coverage | Important nulls and behavior |
|---|---:|---|---|
| `hf_observations` | 234,222 | 24,991 repos, 34 days | 165,000 top; 64,000 newest; 5,222 OpenRouter-linked detail observations |
| `hf_repositories` | 230,863 | 24,991 repos, 34 days | 3,359 excess cross-leg observations removed; `created_at`, `downloads_30d`, `likes` 0% null; `last_modified` 97.7%; `base_model` 34.5%; `library_name` 18.8% |
| `openrouter_models` | 14,862 | 504 route IDs, 34 days | 58.6% of rows have no HF ID; `created_at` and `canonical_slug` 0% null; 9,726 rows have route ID different from canonical slug |
| `arxiv_papers` | 0 | no arXiv Bronze yet | Existing completed days stay untouched; new daily ingestion begins on the next collector run |
| `model_platform_links` | 203 | explicit OpenRouter route → HF repo pairs | 165 distinct HF targets; some routes share a repo |
| `hf_arxiv_links` | 10,518 | 7,386 HF repos and 1,194 arXiv IDs | Links mean *metadata reference* only |
| `hf_repo_cohorts` | 24,991 | one row per first-observed HF repo | `created_at` and age at first observation present for all repos |

The snapshot range includes only 34 days. HF's top 5,000 and newest 2,000 are
selected samples; a repository outside both can still exist and have downloads.
The HF detail leg is restricted to IDs explicitly declared in OpenRouter. Null
rates above refer to the canonical daily HF table. Within-source duplicate
keys, malformed records and invalid nonnegative metrics in this replay: **0**.

## 2. Cross-source coverage

- 6,158 of 14,862 OpenRouter daily rows (41.4%) declare a nonempty HF ID.
  Across history these point to 165 distinct HF repositories. Ten of these
  referenced repositories have no matching HF observation in the canonical
  table; unavailable or gated repositories are valid unmatched data.
- 7,386 of 24,991 observed HF repos (29.6%) carry an explicit `arxiv:` tag.
  They reference 1,194 distinct canonical arXiv IDs. **Zero are resolved**
  against arXiv Bronze because no arXiv snapshot or backfill exists yet.
- 18,558 repos appeared in the newest leg, 6,831 in the top leg, and 162 in
  the OpenRouter-linked detail leg. Across all days, 549 are in both newest and
  top, 4 in newest and detail, and 8 in top and detail. These populations must
  not be added together.
- `openrouter_models.is_alias` means route `id != canonical_slug`. There are
  9,726 such daily rows, while 398 distinct canonical slugs appear across the
  history. A route is not counted as an independent underlying model merely
  because it has a separate catalogue row.

## 3. Lifecycle feasibility

| Question | Status | Reason |
|---|---|---|
| HF creation → first observation | Possible but biased | Source creation timestamps are present, but first observation depends on sampling and collector start. Median observed delay is 1 day. |
| First observation → first nonzero `downloads_30d` | Possible but biased | 14,886 cohorts start at zero; 12,719 later have a positive rolling value within the window. Timing is interval-censored by daily snapshots. |
| Newest → top 5,000 | Possible now for observed transitions | 543 repos first appeared in newest and entered top on a later snapshot day. Left and right censoring remain. |
| HF → OpenRouter appearance | Requires more history | Five linked repos have HF observation before their first OpenRouter observation; 150 are first observed on the same day. Most catalogue models predate collection. |
| arXiv reference → HF availability | Unsupported yet | HF tags are available, but arXiv Bronze is not. Even with metadata, a tag alone does not prove research-to-model causality. |
| HF/OpenRouter → rankings | Unsupported | The archived ranking HTML has a changing serialized payload and unverified ordering/metric semantics. |

## 4. Concrete early observations

- `0bserverx/Qwen3.8-27B-Heretic-GSQ-RCO-GGUF` was first seen in newest on
  2026-09-21 and in top on 2026-09-23. This is a repository transition, not a
  claim about the underlying Qwen family.
- `ibm-granite/granite-4.2-8b` was observed on HF on 2026-08-29 and first
  referenced by an OpenRouter catalogue route on 2026-09-01. The observed lag
  is three days; the actual date OpenRouter began serving it may differ.
- `XiaomiMiMo/MiMo-V2.6-Flash-RL` and `XiaomiMiMo/MiMo-V2.6-Pro-RL` were
  observed on HF on 2026-09-22 and first referenced by OpenRouter on
  2026-09-23. The explicit HF ID supports the cross-platform link.
- More than half of first-observed HF repos (14,886 of 24,991) had zero
  `downloads_30d` at that observation. This is why the newest leg matters for
  early trajectories; a top-only sample would select on the outcome.

## 5. Problems and interpretation limits

1. OpenRouter's serialized ranking HTML changed across early (2026-08-28),
   middle (2026-09-13), and late (2026-09-30) snapshots. The first lacks the
   `queries` state shape used later; the middle has one query; the late has
   three. The late state contains 20 records with a single date and fields
   including `total_prompt_tokens`, `count`, and `total_usage`, while a separate
   `rankingType` says `week`. Their displayed ordering cannot yet be shown to
   correspond to any one field. No ranking table was published.
2. OAI datestamp tracks metadata modification, including administrative and
   bibliographic changes. It is not a paper publication timestamp. arXiv has
   no Bronze history here yet. A retrospective backfill will be marked with
   its true collection time.
3. The HF detail endpoint includes `lastModified`; list endpoints generally do
   not. The 97.7% canonical null rate reflects source shape, not a parsing
   error. Creation timestamps are much more useful for the cohort question.
4. HF repositories include fine-tunes, quantizations, adapters and reuploads.
   A repository, an OpenRouter route, a canonical slug, and an underlying model
   family are different entities. The `base_model` tag is present in 65.5% of
   canonical daily HF rows but does not always establish a complete lineage.
5. `downloads_30d` is a rolling 30-day repository metric. It is neither daily
   downloads nor production usage. First nonzero time is coarsened by daily
   observation and can reflect a small download count.
6. An HF `arxiv:` tag says only that the repository metadata references a
   paper. It cannot establish official authorship, release sequence, or cause.

## 6. Recommended capstone direction

**Choose B: early traction → sustained traction cohorts**, framed as a Data
Engineering measurement system rather than an adoption claim. The newest leg
captures repositories at zero, the top leg observes later entry into a high
download stratum, and `created_at` plus collector `first_seen_date` make the
cohort definition reproducible. It already has measurable transitions, while
more months will permit 7/30/60/90-day windows and censoring rules. Gold should
publish cohort eligibility, observed follow-up duration, rolling download
values, top-entry events and coverage per cohort before any survival or
trajectory comparison.

| Direction | Assessment |
|---|---|
| A. General lifecycle tracking | Good umbrella and architecture; too broad as one measured claim. |
| B. Early → sustained traction | Best current data quality, longitudinal value, reproducibility, and engineering story. |
| C. arXiv → HF → OpenRouter diffusion | Worth a descriptive side analysis after arXiv backfill; paper links are weak evidence and OpenRouter transitions are sparse. |
| D. Model-family / successor lifecycle | Potentially valuable, but canonical family identity and derivative semantics need substantial validation. |

Next: run a small, explicit arXiv OAI backfill to measure tag resolution, then
extend collection until at least 60/90-day cohorts mature. Gold can start with
HF cohorts now, using documented eligibility and censoring rules.
