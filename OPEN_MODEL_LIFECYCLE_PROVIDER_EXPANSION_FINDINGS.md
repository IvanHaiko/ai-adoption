# Provider expansion: first observed state (2026-10-01 UTC)

The [API spike](docs/provider_sources_spike.md) defines the interfaces and
limits. Gold metrics remain at logic version 1 and are unchanged.

## HF inference-provider mapping

The first immutable Bronze snapshot contains **6,437** text-generation HF
repositories returned by the provider-filtered Hub list, with **6,583**
provider mappings, over **7** API calls. All 6,437 returned repos have at least
one mapping. This is not a check of every HF repository; outside the returned
catalogue, provider state is unknown. The source-side mapping is keyed to the
exact HF repo ID, with no fuzzy resolution.

| Provider mappings per returned repo | Repositories |
| ---: | ---: |
| 1 | 6,362 |
| 2 | 45 |
| 3 | 13 |
| 4 | 8 |
| 5 | 3 |
| 6 | 2 |
| 7 | 2 |
| 8 | 1 |
| 11 | 1 |

Fourteen provider names appear. `featherless-ai` dominates with **6,404**
mappings; the next counts are `novita` 56, `deepinfra` 42, `nscale` 16,
`together` 12, and `cohere` and `zai-org` 10 each. Mapping status is `live`
6,554, `error` 27, `staging` 2. Thus simple provider presence or provider count
does not yet mean healthy, independent multi-provider availability. The raw
status and mapping are retained for later analysis.

Exact ID overlap: 754 of 25,800 HF repos ever in project Silver; 598 of 7,067
HF repos observed in the same day's ordinary HF legs; **15 of 19,103** primary
newest-cohort repos; 82 of 18,112 repos first observed since 2026-09-01. The
provider signal currently has very sparse primary-cohort coverage. This is
descriptive overlap, not a causal or adoption result.

Other exact relationships in existing Silver: 165 distinct HF IDs explicitly
declared by OpenRouter, 69 of which appear in the provider catalogue; 7,514 HF
repos have explicit arXiv tag references, 409 of which appear in the provider
catalogue. These sets can overlap and must not be added. No provider mapping is
interpreted as an OpenRouter or arXiv relationship.

## Replicate and cross-source overlap

Replicate's documented public-model endpoint returned HTTP 401 without a token.
After the token was placed in the local user environment, an authenticated
bounded spike sampled **225 rows / 191 distinct models** across newest-created,
oldest-created, and latest-version sortings (three 25-model pages each). All
nine sampled pages had a next cursor: **total public models remain unmeasured**.
The raw public pages and manifest are saved separately as a spike, not a daily
Bronze snapshot.

Among the 191 unique models, `run_count`, model `created_at`, and latest-version
`created_at` were populated for all 191. `run_count` had minimum 1, median
6,559, 90th percentile 1,270,201, and maximum 116,343,122. This is times a
model has been run on Replicate, not unique users or production deployment.
One observation cannot establish monotonicity, resets, or a trajectory.

Structured explicit links: 26 `weights_url`, 16 direct HF repo URLs, **3 exact
matches** to the current HF Silver universe; 75 `paper_url`, of which 40 are
parseable arXiv URLs and none match current arXiv Silver; 103 `github_url`.
The three exact HF matches are `qwen/qwen3-235b-a22b-instruct-2507` →
`Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`, `openai/gpt-oss-120b` →
`openai/gpt-oss-120b`, and `ibm-granite/granite-4.2-8b` →
`ibm-granite/granite-4.2-8b`. Link method is **explicit_weights_url**. No
model-name matching was used. These are biased sample counts, not population
coverage rates.

The official `language-models` collection held 44 models, with `run_count` on
all 44, 35 marked official, and 6 HF weights URLs (5 matching current HF
Silver IDs). Its raw response is saved with the spike. It includes closed models
and is not an exhaustive open-text-generation catalogue. The general public
model list has no stable task/modality field. Consequently a daily scoping rule
appropriate to this project is **not yet justified**. No Replicate Bronze/Silver
leg, entity-link table, run-count delta, or scheduled collection was added.

## Cost and quality

The 2026-10-01 complete collector run took **75 seconds** and **178 HTTP
calls**; seven calls belong to the new HF provider leg, versus approximately
171 without it on this run. The preceding README's ~41-second baseline dates
to 2026-08-29 and is not a like-for-like runtime comparison. The API spike's
seven-page walk took roughly five seconds. The new compressed file is 308,889
bytes: at unchanged size, about **9.3 MB per 30 days** or **113 MB per 365
days**; catalogue growth can change this. The full day's raw directory is
4,245,730 bytes. The provider leg adds no per-model request fanout.

The Replicate spike used nine API calls and about 48 seconds, storing 2.59 MB
compressed across three biased strata. At 25 results/page and unknown total
catalogue size, full daily request and storage cost cannot be estimated from
this sample. A fixed targeted scope would be cheaper but needs a defensible
discovery rule first.

Bronze audit: **0 errors, 0 warnings** across 35 days. Silver: **0 errors, 0
duplicate provider models, 0 duplicate `(day, repo, provider)` mappings**.
Gold rebuild after the new day: **0 DQ errors**, with metric definitions
unchanged. The source is replayable from raw page bodies and records exact
request/fetch provenance. The first observed mapping date is 2026-10-01 or
later, never an inferred provider onboarding date. A model disappearing from
the filtered list remains **unobserved**, not a confirmed removal.

## Decision matrix

| Signal | Historical value / quality | Identity and cohort fit | Cost and decision |
| --- | --- | --- | --- |
| HF provider diffusion | Current mapping status may be irrecoverable later; raw schema and exact HF IDs are good, but catalogue absence and `error` status complicate interpretation | Exact HF ID; only 15 current primary-cohort repos overlap | **KEEP COLLECTING DAILY** at seven bulk pages now. Preserve history, revisit utility after several weeks; avoid Gold use yet. |
| Replicate presence | Public model list works with a token; 191 sampled distinct models span modalities and the curated language-model collection includes closed models | 16 HF weights URLs, 3 exact matches in this biased sample | **KEEP AS EXPERIMENTAL**; no scheduled ingestion without an auditable open-model discovery rule. |
| Replicate run-count trajectory | Present for all 191 sampled models and likely valuable to preserve; resets and daily deltas remain untested | Only 3 exact HF matches in the sample | **KEEP AS EXPERIMENTAL**; define a fixed explicit-link tracking set before daily collection. No zero fill or usage claim. |

The next Replicate gate is a defensible fixed tracking universe and discovery
process, followed by repeated snapshots to check `run_count` revisions and
decreases. The token belongs only in environment/CI secrets, never Bronze
payloads, manifests, Git, or documentation.
