# Provider-source API spike (2026-10-01 UTC)

This spike precedes the new daily source. It uses official API documentation and
live HTTP responses. The first observations are **not** historical launch dates.

## Hugging Face Inference Providers

Official [Hub API documentation](https://huggingface.co/docs/inference-providers/en/hub-api)
supports `inference_provider=all`, a `pipeline_tag` filter, and
`expand=inferenceProviderMapping` on `GET /api/models`. The selected request is:

`GET https://huggingface.co/api/models?inference_provider=all&pipeline_tag=text-generation&limit=1000&expand=inferenceProviderMapping`

The list advertises the next cursor in the HTTP `Link` header. An anonymous
live request succeeded. The seven-page trial returned 6,436 unique models and
6,582 mappings, 2,187,211 response bytes. The later stored first snapshot
returned 6,437 models and 6,583 mappings: the catalogue changed between passes.
The response identifies each HF repository by `id`; each mapping in the live
array had `provider`, with observed `providerId`, `task`, `status`, and optional
`features`, `performance`, and `providerDetails`. Mapping `status` included
`live`, `staging`, **and `error`**. Preserve the whole mapping; do not enforce
only the two statuses shown in the documentation example. These are current
mapping states, with no historical activation timestamp or public history API
established by this spike.

The [Hub rate-limit documentation](https://huggingface.co/docs/hub/en/rate-limits)
places model-list requests in the Hub API bucket, uses five-minute windows, and
currently lists 500 anonymous API requests per window (subject to change). The
trial response advertised `"api";r=499;t=288`. The collector's sequential
request pacing and retry policy apply; a failed page leaves the leg partial.

| Daily design | Approximate additional requests | Assessment |
| --- | ---: | --- |
| Every known HF repo | At least 25,800, growing | Excessive and incomplete if gated |
| Primary/newest cohorts | About 19,103 individual requests now | Still excessive |
| Recently seen plus ever provider-observed | More than 18,000 recent repos now, plus tracked history | Too large without further rotation; misses providers on older repos |
| Bulk provider-filtered text-generation list | 7 observed pages | Selected; complete current provider catalogue within documented scope |

The tracking universe is **the current provider-filtered text-generation list**,
not all HF repositories. The 20-page/20,000-row safety bounds prevent an
unnoticed explosion; hitting them marks the leg partial instead of publishing a
truncated catalogue. This captures entries, mapping count changes, provider
changes, and status changes for models returned on successive days. It does not
prove the last provider was removed when a model disappears from the list:
listing/filtering changes and transient catalogue behavior remain possible.
`first_seen_provider_date` means first collector observation only. Optional
performance values remain in the raw mapping, without a Silver metric contract.

The live first snapshot is seven calls, 2,190,936 raw JSONL bytes including
envelopes, and 308,889 compressed bytes. Each envelope records source URL,
`fetched_at`, HTTP status and page number. The manifest records scope, page/row
counts, SHA-256, bytes, and collection status. Finished older days are not
reopened.

## Replicate

The official [HTTP API reference](https://replicate.com/docs/reference/http/)
documents `GET https://api.replicate.com/v1/models` as a paginated list of
public models and `GET /v1/models/{owner}/{name}` for detail. The list accepts
`sort_by=model_created_at` or `latest_version_created_at`, with descending
ordering by default; pagination uses `next`. The model examples show owner,
name, description, `run_count`, `github_url`, `paper_url`, and `latest_version`.
The metadata update contract includes `weights_url`, but its presence and fill
rate in list results are **unverified**. The creation timestamp's presence in
responses, latest-version timestamp coverage, text-generation classification,
stable identifier coverage, and explicit HF/arXiv overlap are likewise
unverified. The [Replicate changelog](https://replicate.com/changelog/2023-03-21-more-useful-metadata-from-the-model-api)
defines `run_count` as how many times the model has been run; it is not users,
deployments, or market share. Monotonicity and resets require observed history.

All API requests require `Authorization: Bearer <token>` according to the
[reference](https://replicate.com/docs/reference/http/). A live anonymous list
request returned **401 Unauthenticated** before the token was supplied. The
documented rate limit for non-prediction endpoints is
3,000 requests/minute, with HTTP 429 on throttling. The public
[OpenAPI schema](https://replicate.com/docs/reference/openapi/) was accessible
without a token, but it is no substitute for a representative live sample.

An authenticated bounded sample was completed later on 2026-10-01, after a
token was supplied in the Windows user environment as `REPLICATE_API_TOKEN`.
The script `python -m scripts.replicate_spike` requested three 25-model pages
each from newest creation, oldest creation, and latest version order: **9
calls, 225 rows, 191 distinct owner/name IDs**. Every stratum still advertised
a next page, so the total public catalogue size is **unknown**. This is a
deliberately biased sample, not a catalogue-wide rate. The exact public
response bodies and request/fetch provenance are under
`data/spikes/replicate/2026-10-01/`; the token is never written there.

In the 191 distinct models, `run_count`, model `created_at`, and latest-version
`created_at` were present in all 191. Observed `run_count` ranged from 1 to
116,343,122 (median 6,559; 90th percentile 1,270,201). There were 26
`weights_url` values, 16 pointing to HF repos, and only **3 exact IDs** already
in this project's HF Silver universe. `paper_url` was populated for 75 models;
40 were parseable arXiv URLs, none matching the project's current arXiv Silver
universe. `github_url` was populated for 103. These are sample counts, not
population estimates. Neither `run_count` monotonicity nor its daily increment
can be tested from one observation.

The [collections API](https://replicate.com/docs/reference/http/) returned a
`language-models` collection of **44** models, all with `run_count`; 35 were
marked official and only 6 had HF `weights_url` (5 matching project HF IDs).
The raw collection response is preserved beside the sampled list pages. It includes closed models and
is curated rather than an exhaustive text-generation or open-model filter.
The public list has no task/modality field; names and descriptions do not make
an auditable scope, while search is documented as beta. Nine calls took about
48 seconds in this environment; their compressed raw sample was 2,593,471
bytes, with one newest-created stratum dominating storage. A full-catalogue
daily call/storage estimate cannot be justified without walking the entire
catalogue, which this spike intentionally avoided.

**Decision:** the API offers a potentially useful longitudinal platform run
counter, but a reproducible open-model tracking universe and useful explicit
overlap are not established. Keep the sampled source experimental; do not add
Replicate Bronze/Silver or a daily scheduler leg yet. A later targeted design
could track a fixed, explicitly HF-linked Replicate ID set, with a separate
documented discovery method. Do not use model-name similarity to enlarge it.

## Time and missingness contract

HF repo `created_at`, HF project `first_seen_date`, provider
`first_seen_provider_date`, Replicate model/version creation time, snapshot date,
and `run_count` observation time describe different events. No collector date
is a platform launch date. A source failure is not an empty catalogue; a missing
mapping is not numeric zero, and a missing later model is not a confirmed
permanent removal.
