# Persistent Hugging Face cohort validation

The recommended cohort is `hf_lifecycle_20261015_v2`: 1,500 fixed repositories
selected from 32,983 eligible candidates before the 2026-10-13 23:59:59 UTC cutoff.
Both v1 and v2 registries are retained. The implementation, registry bytes and
committed test sources are unchanged by documentation preparation.

## Checks

- The full project suite passes 199 tests; the two cohort test modules account
  for 65 of those tests. Tests use local fixtures and fake HTTP responses.
- Ruff passes with the repository's configured rules.
- An automated independent read-only review of the implementation and v2
  selection on 2026-10-15 accepted the change with no blockers. This was an
  automated review; it does not represent human approval of deployment.
- Independent recomputation matches all v2 members, seed/hash ordering and
  500/500/500 quotas. Popular and recent ordered membership is unchanged;
  the remaining group retains 19 and replaces 481 v1 members.
- Reproduction in a fresh directory produces the exact v2 registry bytes,
  including when the preserved v1 source uses CRLF line endings. Derivation
  from v1 reads no Raw snapshots or live APIs.
- Registry initialization is immutable. Historical completed-day collection is
  a zero-request no-op. Failures remain missing observations, and the response
  body encoding preserves multiline HTTP payloads losslessly.

The public check summary is in
[v2 validation results](verification/hf_cohort_v2/results.json). Historical v1
[registry reproduction](verification/hf_cohort/offline_validation.json) and
[readiness measurements](verification/hf_cohort/readiness.json) remain available
as versioned technical evidence.

## Reproduce locally

Install the dependencies in `requirements-dev.txt`, then run from the repository
root:

```sh
python -m pytest -q tests/test_hf_cohort.py tests/test_hf_cohort_v2.py
python -m pytest -q
python -m ruff check .
```

See the [architecture freeze](hf_cohort_architecture_freeze.md) for the exact
fresh-directory reproduction command, timestamps and expected registry SHA-256.
The current implementation adds no production dependencies.

## Deployment boundary

Validation is local. No push, merge, repository-variable update, cloud change
or live collection was performed. A successful scheduled v2 run remains
unverified. Historical Raw/Bronze/Silver/Gold and identity mappings are preserved.

After deployment approval, deploy the feature and both registries, set
`HF_COHORT_ID=hf_lifecycle_20261015_v2`, and inspect the first newly collected
snapshot day. Existing cron slots and concurrency controls are reused. An
already closed day remains unchanged; no historical backfill is required.

Additional documentation:

- [Technical behavior and configuration](hf_cohort_tracking.md)
- [Integration decision](hf_cohort_integration.md)
- [Retained v1 readiness assessment](hf_cohort_readiness.md)
- [Synthetic coverage example](examples/hf_cohort_coverage.json)
