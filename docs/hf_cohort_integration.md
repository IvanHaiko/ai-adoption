# Cohort integration decision (before implementation)

Decision recorded 2026-10-14 after inspecting collectors, manifests, storage,
Silver/Gold, temporal identity, audit, tests, scheduler and the storage ADR.

Add `hf_cohort` after discovery in `collector.run`, preserving all existing
legs. It uses `Context`, the sequential retrying `HttpClient`, deterministic
gzip storage and per-day manifests. Reuse valid same-day measurements using
existing precedence: detail (`hf_models`), top, newest. Keep original timestamps
and all matching discovery provenance. Missing measurements use exact-ID GETs.
Record failures separately and retain fixed members. Silver gains additive
cohort provenance and reads valid cohort rows even when its leg is partial;
canonical repository/day precedence remains discovery-first.

Persist an offline-initialized JSON registry under `data/cohorts/`, with cohort
ID, version, cutoff, selection timestamp, candidate inputs and hashes, fixed
members, strata and enrollment. Commit it with the feature before operational
activation. No database, storage abstraction or new service is needed.

Use all eligible historical discoveries before cutoff, rather than just the
latest lists. Inspection found 47 observed days (2026-08-28 through 2026-10-13),
32,986 IDs, 7,054 latest-day IDs and 9,711 IDs with at least five observed days.
Latest metadata included creation and download attributes. Start with 500
popular, 500 recent from unselected candidates and 500 remaining, spreading the
last group across organizations. Selection is deterministic and fixed. This is
a sample of the previously discovered population; ecosystem representativeness
is not claimed. Historical availability is recorded, without requiring a
minimum number of observations that would exclude short-lived discoveries.

HF documents paginated discovery and single-repository `model_info`; its
documented list interface has no arbitrary exact-ID batch parameter. Preserve
the existing single-repository integration. Cohort concurrency is one. A
0.75-second request floor is below the currently documented anonymous limit
of 500 API requests per five-minute window; limits may change and a shared
runner IP can consume the same quota. Existing three-attempt retries and
timeout behavior apply. Maximum primary extra requests are 1,500 minus reused
measurements, with up to 4,500 attempts if every call exhausts three retries.

The checked-in deployment is GitHub Actions, four UTC slots daily with a
concurrency guard and durable commits of `data/raw`. The existing ADR postpones
S3 until the capstone. No current AWS deployment configuration or billing
evidence was found in the repository; live account resources were not queried.
This design creates no AWS resources and has $0 incremental AWS cost. Actions
duration/storage estimates must be measured against the actual selected cohort
before activation. No remote repository variable or live schedule is changed.

Keep historical Raw/Bronze/Silver/Gold and identity mappings immutable. All
validation output and research readiness artifacts go to separate directories.
Old complete days remain byte-identical no-ops; never poll the live API under a
historical cohort snapshot date. Redirected identities are diagnostics requiring
explicit reconciliation, not automatic membership replacements.

Validation covers deterministic enrollment, immutable registries, same-day reuse,
failure handling, partial reruns, UTC boundaries and additive Silver provenance.
Offline reproduction and regression results are documented in the
[validation report](hf_cohort_validation.md).

References checked on 2026-10-14:

- [HF rate limits](https://huggingface.co/docs/hub/en/rate-limits)
- [HF list and model APIs](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)
- [HF download measurement documentation](https://huggingface.co/docs/hub/en/models-download-stats)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Existing storage ADR](adr/0001-snapshots-live-in-git-until-s3.md)
