# Final cohort selection and architecture freeze — 2026-10-15

The corrected cohort is **`hf_lifecycle_20261015_v2`**, selection version **2**.
Its 1,500 unique repositories use exactly the original 32,983 eligible candidates
and discovery-input records from v1, with the same cutoff,
**2026-10-13 23:59:59 UTC**. No observations after that cutoff are read. The v1
registry and historical Raw/Bronze/Silver/Gold data remain unchanged.

| Group | v2 members | Retained from v1 | Removed / added | v2 organizations |
| --- | ---: | ---: | ---: | ---: |
| Popular | 500 | 500 | 0 / 0 | 145 |
| Recent | 500 | 500 | 0 / 0 | 237 |
| Remaining | 500 | 19 | 481 / 481 | 500 |

Popular and recent membership and ordering are unchanged. **481 of 500 remaining
members changed**; the group retains 19. No repositories occur in multiple groups.

## Fixed algorithm

Seed: **`ai-model-lifecycle/hf-cohort/v2`**.
Algorithm: **`sha256-organization-round-robin-v2`**.

Popular and recent selection, eligibility, exact repository identities, and their
deterministic tie-breaks retain the v1 rules. Only the remaining pool changes:

1. Group by recorded author, falling back to repository namespace.
2. Hash UTF-8 bytes of compact JSON `[seed,"organization",organization]`, using
   SHA-256, `ensure_ascii=False`, separators `(',', ':')`. Order organizations by
   hexadecimal digest, then original string on a digest tie.
3. Order repositories inside each group the same way, with domain `"repository"`
   and the exact repository ID. No Unicode or case normalization occurs.
4. Repeatedly take one repository per nonempty group in the fixed hash order
   until the remaining quota, including any quota shortfall, is filled.

Alphabetical organization order no longer determines the selected prefix.
Different seeds can yield different remaining members; the published seed is
fixed. Original-string tie-breaks apply only after equal hash digests. The registry
records its algorithm, seed, encoding, candidate/input digests, source-registry
canonical digest, and actual selection/enrollment timestamp.

This is a purposive sample of the previously discovered population, **not a
representative sample of the HF ecosystem**. Discovery filters, popularity and
recency quotas, differing measurement ages, namespace definitions and missing
metadata remain biases. Organization round-robin favors diversity over uniform
repository inclusion probability; large organizations receive less per-repository
weight. A finite fixed sample retains sampling variation. HF downloads remain
rolling 30-day measurements; failed collections remain missing observations.

## Byte-for-byte reproduction

Use a fresh local directory. The commands copy only the preserved v1 registry;
neither Raw nor live APIs are needed:

```sh
python -c "from pathlib import Path; import shutil; p=Path('.local-notes/cohort-v2-replay/data/cohorts'); p.mkdir(parents=True,exist_ok=False); shutil.copyfile('data/cohorts/hf_lifecycle_20261014_v1.json',p/'hf_lifecycle_20261014_v1.json')"
python -m collector.cohort --root .local-notes/cohort-v2-replay \
  --cohort-id hf_lifecycle_20261015_v2 --from-cohort hf_lifecycle_20261014_v1 \
  --cutoff 2026-10-13T23:59:59+00:00 --selected-at 2026-10-15T08:40:08.947806+00:00 \
  --size 1500 --seed ai-model-lifecycle/hf-cohort/v2
python -c "from pathlib import Path; import hashlib; a=Path('data/cohorts/hf_lifecycle_20261015_v2.json').read_bytes(); b=Path('.local-notes/cohort-v2-replay/data/cohorts/hf_lifecycle_20261015_v2.json').read_bytes(); assert a==b; print(hashlib.sha256(b).hexdigest())"
```

Expected SHA-256 of the generated LF JSON bytes:
`24e984610dd9efb073d2dce686d70ca8360ddfb8b23ba7a957059ef596d4d077`.
Source-registry canonical hashing ignores checkout line endings; candidate lists,
timestamps, seed and JSON serialization remain fixed. A path-specific `-text`
Git attribute preserves the v2 JSON bytes across Windows/Linux checkouts; v1's
existing attributes and file remain unchanged.
Reinitializing an existing cohort ID preserves its file; it is not itself a
fresh reproduction proof. `selection_version=1` remains available for v1 replay.

## Validation

Targeted tests cover seeded order, organization hash ordering, different seeds,
round-robin diversity, real 500/500/500 membership, v1 compatibility, unchanged
eligibility/cutoff, input tampering, immutable initialization, and fresh byte
reproduction even with CRLF source checkout and an unreadable later Raw manifest.
The existing collection tests, full project suite and Ruff are required checks.
The automated independent review and check outcomes are recorded in
[v2 validation results](verification/hf_cohort_v2/results.json).

## Architecture decision and production activation

The existing ingestion architecture, persistent cohort tracking, and historical
data conventions are **accepted and frozen for the capstone**. No additional
infrastructure, frameworks or pipeline redesign is planned before the capstone.
This is a project decision, not a restriction on correcting actual defects.
The next phase is operational collection, data quality monitoring, analytics
and capstone presentation preparation.

Production remains **inactive for cohort tracking**. This task performs no push,
merge, repository-variable change, AWS change, or live collection trigger.

After explicit deployment approval:

1. Deploy the reviewed branch changes and **both** immutable registries to `main`.
   Retain v1 for audit; select v2 for operation.
2. Set repository variable **`HF_COHORT_ID=hf_lifecycle_20261015_v2`**. The existing
   workflow already passes that variable to the CLI and reads the registry from
   the checkout. Preserve its concurrency guard and four UTC slots: 06:20,
   12:20, 18:20 and 23:40. No workflow edit or new service is needed.
3. Wait for the first new snapshot day collected after activation. Already closed
   days and older manifests without a cohort leg stay unchanged; no historical
   cohort backfill or manual collection is required.
4. Verify `hf_cohort.cohort_id`, requested members, successful/reused/cohort-only
   coverage, failures, deferrals and stored payload hash. Confirm a completed-day
   rerun is a zero-request no-op. A scheduled v2 run has **not** yet been verified.
