# Operational baselines and temporal deployment facts

This stage adds an operational baseline journal and replays `raw_snapshot_index -> deployment_observations -> provider_deployments` for OpenRouter and HF inference-provider mappings. It preserves the approved policy: provider deployments have nullable `canonical_model_id`; source HF repository declarations are evidence for later identity work. No canonical assignments, identity links or lifecycle events are produced.

## Original and active baseline semantics

`data/validation/historical_archive_baseline.json` remains the original forensic baseline. Its exact bytes and the Phase 2 report are retained. The initial operational baseline is a separate dated file, `historical_archive_baseline_2026-10-03_active_001.json`. Both attest the same 252 historical files at activation.

Activations live under `data/validation/baseline_activations/activation-NNNNNN.json`. Each exclusive-created record contains the new baseline path/hash, original baseline path/hash, previous activation hash and baseline path, sequence, time, reason, approved changed paths with before/after hashes, and additions. The enrichment manifest's exact bytes are preserved in base64 evidence and checked against its baseline hash. Previous activation records, baselines, and forensic manifest copies remain available after rollover.

`python -m temporal verify`, `index`, and `observations` select the latest activation by default. They validate the journal sequence/hash ancestry, the original forensic baseline hash, dated baseline hashes, and manifest evidence. With no journal they fall back to the original baseline. An explicit `--baseline` still checks the journal's integrity before selecting that file; it never silently repairs a broken journal.

Publication through `index` or `observations` accepts only the original or a baseline already in the activation journal. Taking an unactivated fingerprint and passing `--baseline` cannot bypass payload protection. `verify` can inspect an arbitrary candidate as a read-only comparison, but that inspection does not activate it.

After a legitimate enrichment run, inspect the changes, freeze a **new dated** baseline, and explicitly activate it:

```powershell
python -m temporal baseline --output data/validation/historical_archive_baseline_<date>_active_002.json
python -m temporal activate-baseline --baseline data/validation/historical_archive_baseline_<date>_active_002.json --reason "Reviewed legitimate arXiv enrichment run" --accept-enrichment-manifest-change
python -m temporal verify
```

Activation refuses deletions and modifications of any protected payload or raw/backfill manifest. The only existing file change it can accept is `data/enrichment/arxiv_ids/_manifest.json`, with the explicit flag and a nonempty reason. New files are recorded as additions; the candidate must cover the entire current archive. The flag records the operator's assertion of legitimacy; code checks path and hash constraints, not the scientific meaning of each enrichment decision. A damaged payload cannot be blessed by taking another fingerprint. Exclusive creation prevents overwriting the original or a dated baseline.

To compare the live archive with the original forensic state:

```powershell
python -m temporal verify --baseline data/validation/historical_archive_baseline.json
```

That explicit comparison is expected to fail after an approved enrichment manifest change, while ordinary verification against the new active baseline passes. The original baseline and archived prior manifest evidence remain intact. These are preservation checks, not filesystem locks or authentication against someone deliberately rewriting all validation evidence.

## Input and publication safety

The indexer is now version 2. It adds `manifest_sha256_file` to snapshot metadata so derived facts attest both source payload and metadata bytes. The existing Phase 2 index is retained; regenerate into a new file for this stage. The fact replay checks the supplied index against a fresh read of all approved snapshots, rejecting stale/altered metadata, new unindexed snapshots and integrity mismatches.

Both `index` and `observations` compare a stored-file fingerprint of the **entire current archive** before/after reading, including files added since the active baseline. This closes the earlier check's gap where an added file could change without changing paths, counts or total size. Commands publish only after preservation succeeds and still restrict destinations to `data/validation` or `data/temporal_v2`. Existing destinations are refused; `--dry-run` creates no output.

```powershell
python -m temporal index --output data/temporal_v2/raw_snapshot_index_deployments_2026-10-03.json
python -m temporal observations --index data/temporal_v2/raw_snapshot_index_deployments_2026-10-03.json --dry-run
python -m temporal observations --index data/temporal_v2/raw_snapshot_index_deployments_2026-10-03.json --output data/temporal_v2/deployment_layer_2026-10-03.json
```

The new JSON bundle has separately grained `deployment_observations`, `provider_deployments`, `source_runs` arrays and `summary`, plus schema/normalizer versions, materialization time and index provenance. JSON preserves nested source values and their types without introducing a new dependency; a future Parquet projection can be derived from the same facts. It is ignored by Git like other derived outputs. The normalized tables do not replace v1 Silver or Gold.

## Deployment observation contract

An observation is one explicit source record: an OpenRouter catalogue row or one HF provider mapping within a response/model. Multiple source records that declare the same provider ID in one snapshot are kept separately with row locators and stable observation IDs. They are counted as collisions requiring later provenance review; no source HF repositories are silently merged into a canonical model.

| Fields | Meaning |
| --- | --- |
| `observation_id`, `deployment_key`, `snapshot_id` | Reproducible hashes. Deployment identity hashes the exact `(provider, provider_model_id)` pair; observation identity also includes snapshot, row locator and source-row semantic hash. `recorded_at` is excluded. |
| `provider`, `provider_model_id`, `canonical_model_id`, `deployment_id_status` | Provider namespace and unmodified source identifier. OpenRouter uses `openrouter`; its ID prefix is retained as `source_publisher`. HF uses each mapping's provider string. Canonical ID remains null. A missing/empty HF provider ID preserves an unkeyed observation and contributes to a diagnostic count; it does not create an invented deployment. |
| `observed_at`, `observation_date`, `observed_precision`, `observed_basis` | Original response capture timestamp and actual UTC date where known. Each HF page supplies its own timestamp, even across midnight. OpenRouter uses its manifest leg `fetched_at`. Date-only fallback leaves `observed_at` null; no midnight or migration-time capture is invented. Ordering compares actual UTC instants while retaining original strings. |
| `effective_at`, `recorded_at`, `is_backfill` | Effective time is null: the source's repo/listing creation metadata does not date the observed price/context/provider state. `recorded_at` is this materialization's actual UTC time, shared within the bundle. Historical replay marks all facts `is_backfill=true`. |
| `presence_state`, `provider_status` | Explicit returned rows are `PRESENT`. HF `live`, `staging`, `error`, etc. are kept separately; presence is not a claim of successful inference. No absent row is fabricated from a missing model or failed run. |
| `price` | Common object with `values`, `source_field`, nullable `currency`, `unit`. OpenRouter's original pricing strings and HF `providerDetails.pricing` numbers remain in their original key/value shape. Their units are not assumed equivalent and no cross-provider price calculation is offered. Missing pricing remains null rather than zero. |
| `context` | `context_length`, `max_completion_tokens`, `source_field`. OpenRouter uses its catalogue context and top-provider completion limit; HF uses provider details. Missing values stay null. |
| `capabilities` | Common nullable slots for source `architecture`, `supported_parameters`, `features`, `task`. Booleans remain booleans and false is not conflated with missing. Source semantics remain visible. |
| `source`, `source_scope`, `source_hf_repo_id`, `source_publisher`, `source_canonical_slug` | Source declarations, not resolved model identity or family membership. |
| `source_evidence` | Archive-relative snapshot/manifest paths and hashes; JSON/JSONL source row locator, semantic source-row hash, response URL, original collection day and run/leg status. The original payload's stored/raw hashes retain byte provenance even though the source-row hash uses canonical JSON serialization. Absence confirmation is always false. |

## Provider deployment contract and absence

`provider_deployments` contains one row per exact provider/ID pair with `deployment_key`, nullable `canonical_model_id`, first/last observed dates and timestamps, observation/snapshot counts, and a list of declared `source_hf_repo_ids`. That list is evidence aggregation only. If the first/last day's timestamp is only day-granular, the corresponding instant remains null rather than selecting a later precise timestamp.

`current_status` is presence evidence at the source cutoff, with `as_of_collection_day`, `status_basis` and `latest_source_states`; it is not a wall-clock hosting status. A returned record in the latest applicable source run is `PRESENT`; missing evidence is `UNKNOWN`. If several source feeds cover a deployment, the summary conservatively requires all of their latest states to be present. Failed runs with no payload, skipped/missing legs and older first-observed deployments remain visible through `source_runs` and the source states. An absent record in a successful catalogue still remains unknown until the separate provider completeness policies are approved. There is no `ABSENT_CONFIRMED` or delisting generation in this stage.

## Validation and current measured coverage

The historical dry-run produced 28,959 observation facts from 38 relevant payloads: 15,790 OpenRouter catalogue rows (36 snapshots) and 13,169 HF mapping rows (two snapshots). They describe 7,091 provider-scoped deployments. Of those, 7,046 have positive presence evidence at the 2026-10-02 cutoff; 45 are unknown. Six deployment/snapshot pairs have multiple source rows and retain their separate evidence. No canonical identities were assigned.

The saved v1 Silver output ends at 2026-10-01: its 15,326 OpenRouter rows and 6,583 HF provider mapping rows are therefore not comparable to the new layer's full cutoff without accounting for the extra day. The report compares the matching overlap and explains the additional rows. V1 outputs and all historical payloads/manifests remain unchanged during this stage. The original Phase 2 index and report are also retained.

Tests cover baseline rollover with original evidence retained, explicit acceptance and payload rewrite/deletion rejection, broken journal/dated-baseline/original evidence, mutation of additions during indexing, exact provider namespaces and IDs, per-page capture times and timezone ordering, unknown capture precision, duplicate source evidence, missing provider IDs, null units/effective dates, status vs presence, missing/partial later runs, repeatable IDs, stale/altered index rejection, guarded publication and dry-run. Existing v1 tests remain part of the complete suite. The final suite passed 75 tests and Ruff. Final validation results are stored in `data/validation/deployment_layer_validation_2026-10-03_final.json`; the earlier materialization report is retained separately. The final report attests implementation-file hashes, both baseline checks, the exact materialized layer hash and matching v1 overlap projections. All 252 protected historical files retain their original stored-byte hashes.

Changed existing files: `temporal/index.py`, `temporal/__main__.py`, `tests/test_temporal_archive.py`, `docs/temporal_archive_phase2.md`, `docs/temporal_model_migration_plan.md`. New modules/tests: `temporal/baselines.py`, `temporal/deployments.py`, `tests/test_temporal_baselines.py`, `tests/test_temporal_deployments.py`, plus this contract and dated validation artifacts. No collector module, workflow, v1 schema or historical source file is changed by these operations.
