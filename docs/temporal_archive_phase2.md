# Archive preservation and raw metadata index (Phase 2)

The implemented layer reads historical payloads and manifests and writes new validation or metadata files. Existing collectors, Silver and Gold retain their current interfaces. No lifecycle event engine, provider normalization or canonical identity assignment is introduced.

## Protected history and baseline

`data/validation/historical_archive_baseline.json` freezes every existing file, including manifests, under `data/raw`, `data/backfill/arxiv`, and `data/enrichment/arxiv_ids`: 252 files / 74,914,566 stored bytes at creation. Replicate spikes are excluded. SHA-256 hashes cover the **stored file bytes**, including gzip headers and manifest formatting. Manifest `sha256_raw` separately attests decompressed payload bytes. This detects recompression as a change even if decompressed content remains identical.

The baseline records schema version, generation time, protected roots, file count, total bytes, per-path stored byte count and stored-file hash, and minimum/maximum capture timestamps found in existing manifests. Those bounds use only manifest `fetched_at`/`collected_at`; they are not an exhaustive response capture range or model release range. The index exposes envelope capture times separately.

```powershell
python -m temporal verify
python -m temporal index --dry-run
python -m temporal index
```

Verification fails on any frozen path disappearing or changing size/hash; it reports newly added files separately and accepts additions. The index command verifies preservation before and after reading the archive and rejects a changing inventory before publishing. Output paths are restricted to `data/validation` and `data/temporal_v2`; neither command can write to raw, enrichment, backfill, Silver or Gold. Baseline and index creation use exclusive file creation and refuse an existing destination. To produce another index, use a new `--output data/temporal_v2/<new-name>.json`. `--dry-run` reads and validates without writing any output or creating its output directory.

For a new, explicitly justified dated baseline:

```powershell
python -m temporal baseline --output data/validation/historical_archive_baseline_<date>.json
python -m temporal verify --baseline data/validation/historical_archive_baseline_<date>.json
```

Do not replace the original baseline to silence a verification failure. Enrichment's existing manifest is mutable during normal resolver runs: such a change will intentionally fail the frozen comparison. Investigate it and retain the original baseline; these checks do not lock the filesystem or prohibit the legacy resolver from writing. New daily directories remain compatible with the frozen path set. The collector is not invoked by any archive command.

## Index contract

`data/temporal_v2/raw_snapshot_index.json` is a derived JSON index, ignored by Git like existing Silver/Gold outputs. JSON needs no additional runtime infrastructure or dependency; payloads remain unchanged. Its top level has `schema_version`, `indexer_version`, `generated_at`, `snapshot_count`, and `snapshots`. The current index includes 210 compressed payloads; manifests are protected by the baseline and referenced by index rows rather than indexed as provider snapshots.

Each row contains:

| Fields | Meaning |
| --- | --- |
| `snapshot_id`, `source`, `source_scope`, `snapshot_path`, `manifest_path` | Stable SHA-256 ID from archive-relative path and stored content hash; source leg and evidenced scope; provenance paths. |
| `collection_day`, `collection_date`, `collected_at`, `collected_until` | Original daily manifest day, actual UTC capture date where known, first/last available response capture timestamps for a multi-response file. Original timestamp strings and fractional precision are retained. Backfill source windows stay in scope and never become collection dates. |
| `observed_basis`, `observed_precision`, `capture_timestamp_count` | Whether timing comes from raw envelopes, daily manifest, enrichment manifest or retrospective `collected_at`. Date-only fallback keeps `collected_at` null; no invented midnight, migration-time observation, filesystem mtime or source creation timestamp. |
| `response_count`, `model_observation_count`, `source_record_count` | Raw detail/list response counts and observed row counts. arXiv record counts are not model observations; Atom resolution items may share a response and are not model counts. |
| `content_hash`, `sha256_file`, `sha256_raw`, `manifest_sha256_raw`, `integrity_status`, `size_bytes`, `bytes_raw` | Stored SHA-256 (`content_hash` alias), computed decompressed hash and existing manifest hash, verified/unattested status and sizes. A manifest payload mismatch blocks indexing. |
| `collector_version`, `parser_version`, `indexer_version`, `schema_version`, `source_schema_version`, `ingestion_run_id` | Only evidenced historical versions are populated. Upstream parser version and original ingestion run ID are null when unavailable. Indexer version is its own version, not a fabricated old collector/parser version. |
| `run_status`, `leg_status`, `resolution_statuses`, `pagination_complete` | Original source/manifest status evidence. Enrichment resolution statuses remain separate from daily run status. Pagination completeness is null unless explicitly recorded; `ok` alone is not promoted to an absence proof. |
| `absence_evidence_grade`, `can_confirm_absence` | Always `not_assessed` and false in Phase 2. Both providers' absence rules remain a later decision. |

Snapshot rows and their IDs are identical on repeat reads of identical history; top-level `generated_at` describes this materialization and may differ. Archive capture times never use that generation time. A payload spanning several responses has a capture interval, so later observation normalization must use individual response timestamps rather than assigning the first capture to every model.

The approved identity policy remains `HF repository -> identity link -> optional canonical_model`. Nullable canonical IDs preserve future family/variant modeling. No identity is inferred by this index.

## Verification and changed files

`tests/test_temporal_archive.py` covers stored-byte and manifest changes, deletions and permitted additions; guarded outputs and baseline overwrite refusal; reproducible index rows/IDs; original capture precision and date-only fallback; retrospective retrieval dates; raw-manifest integrity mismatch; dry-run and failed-verification publication; a continued synthetic v1 collector/Silver/Gold replay; and read-only replay against the real frozen historical archive. All existing tests remain part of the verification gate. A saved run summary belongs in `data/validation/phase2_validation.json`.

Existing files changed in this phase: `.gitignore` (ignore derived v2 metadata) and `docs/temporal_model_migration_plan.md` (accepted policy and phase status). New files: the `temporal` package, `tests/test_temporal_archive.py`, this contract, the historical baseline and Phase 2 validation summary. Collector modules, workflows, Silver/Gold schemas and pre-existing archive files are unchanged by this implementation.

The completed Phase 2 local run passed 54 tests (12 new archive/index tests) and `ruff check .`. Both dry-run and materialized index preserved all 252 frozen file hashes and sizes, with no missing, changed or added protected files. The index contains 210 snapshots and all rows have `can_confirm_absence=false`. The recorded validation summary attests the exact baseline and index hashes used in that run.

Subsequent operational policy: the original baseline is permanent forensic evidence. Ordinary commands now select an explicitly activated dated baseline through an append-only activation journal. A legitimate enrichment manifest change requires a new baseline, a reason and explicit manifest-change acceptance; changing a protected payload still blocks activation. Indexer version 2 adds a manifest stored-byte hash, and before/after checks also cover additions since the active baseline. The Phase 2 index/report remain retained. See `docs/temporal_deployment_layer.md` for the activation and deployment replay contracts.
