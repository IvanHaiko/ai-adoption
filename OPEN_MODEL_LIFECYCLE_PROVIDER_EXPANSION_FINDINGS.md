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

Replicate's documented public-model endpoint returned HTTP 401 without an API
token, and no token was present. **Observed public models: unmeasured.**
Relevant-scope count, `run_count` availability/distribution, model/version
creation coverage, explicit HF/weights/GitHub/paper links, and HF ↔ Replicate
or arXiv ↔ Replicate overlap are unmeasured. No zero counts are asserted. No
Replicate source, entity links, or run-count deltas were added.

## Cost and quality

The 2026-10-01 complete collector run took **75 seconds** and **178 HTTP
calls**; seven calls belong to the new HF provider leg, versus approximately
171 without it on this run. The preceding README's ~41-second baseline dates
to 2026-08-29 and is not a like-for-like runtime comparison. The API spike's
seven-page walk took roughly five seconds. The new compressed file is 308,889
bytes: at unchanged size, about **9.3 MB per 30 days** or **113 MB per 365
days**; catalogue growth can change this. The full day's raw directory is
4,245,730 bytes. The provider leg adds no per-model request fanout.

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
| Replicate presence | Public model API is documented; actual catalogue, modality mix and historical recoverability are unmeasured | Explicit overlap unmeasured | **KEEP AS EXPERIMENTAL** at documentation/spike stage; no scheduled collection until authenticated sample supports a reproducible scope. |
| Replicate run-count trajectory | `run_count` is documented as times run, but availability, resets and deltas are unmeasured | Explicit HF linkage unmeasured | **KEEP AS EXPERIMENTAL**; sample first, then decide daily collection. No zero fill or usage claim. |

The next Replicate gate is an API token supplied through local environment or
CI secret, followed by a bounded authenticated sample and explicit-link audit.
The token must never enter Bronze payloads, manifests, Git, or documentation.
