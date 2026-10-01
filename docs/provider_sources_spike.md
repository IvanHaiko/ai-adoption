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
request returned **401 Unauthenticated**. `REPLICATE_API_TOKEN` was absent in
this environment. The documented rate limit for non-prediction endpoints is
3,000 requests/minute, with HTTP 429 on throttling. The public
[OpenAPI schema](https://replicate.com/docs/reference/openapi/) was accessible
without a token, but it is no substitute for a representative live sample.

**No Replicate catalogue was collected.** Public model count, page size,
`run_count` availability and distribution, creation-date coverage, modality
mix, explicit-link coverage, request count, storage, and daily runtime are
therefore **unknown**, not zero. No Replicate Bronze/Silver leg or scheduler
step is added before those measurements justify a scope. A later spike should
sample the newest and a few older pages, measure explicit `weights_url`, HF URL,
paper/arXiv and GitHub links separately, and decide whether a text-generation
scope is reproducible. Do not use model-name similarity to inflate overlap.

## Time and missingness contract

HF repo `created_at`, HF project `first_seen_date`, provider
`first_seen_provider_date`, Replicate model/version creation time, snapshot date,
and `run_count` observation time describe different events. No collector date
is a platform launch date. A source failure is not an empty catalogue; a missing
mapping is not numeric zero, and a missing later model is not a confirmed
permanent removal.
