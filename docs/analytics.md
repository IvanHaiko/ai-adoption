# Quality and hosting marts

Run `python -m analytics` after collection, or pass `--root PATH` for an isolated
replay. This writes four derived Parquet files and `summary.json` under
`data/analytics/`. Inputs are read-only daily manifests, immutable provider and
cohort payloads and the exact cohort registry selected by each manifest. The
cutoff is the latest archived manifest date; no wall-clock date is used.

- `source_coverage`: source × calendar day, including missing days and legs.
  Before a source's first retained leg its status is `not_started`; subsequently
  missing is `missing`. Source `status=ok` describes collection success, not
  market completeness. Scope, top-list target and source hashes are retained.
- `cohort_coverage`: registered cohort × selection group × collected day,
  with the fixed denominator and missing reasons. No membership replacement or
  inference from a redirected ID occurs. Missing collection days after initial
  panel collection retain the last known registry and have `source_available=false`;
  zero coverage means no measurements collected, not zero model activity.
- `identity_review_queue`: requested and returned IDs for each recorded
  mismatch, awaiting review. Repeated daily failures are retained as evidence.
- `hosting_day`: exact HF repo × observed provider-catalogue day, with distinct
  explicitly `live` providers, all declared providers and original status
  counts. Empty mappings are `unknown`. A count of zero live declarations does
  not prove that inference is unavailable. An unreturned repo has no invented
  row; a failed or partial source retains its status.

Payload SHA-256 values are checked before replay. Every fact points to its
manifest and hashes; hosting facts also have one-based envelope/model locators.
Outputs are deterministic projections and can be replaced by another replay.
The source archive, Silver and Gold are never changed by this command.

Tests: `pytest -q tests/test_analytics.py` verifies missing days, fixed membership,
identity mismatches, distinct providers, unknown state and tampering rejection.
