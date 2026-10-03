# Conservative identity history

Implemented sequence: immutable archive → raw snapshot index → deployment observations → provider deployments → identity history. Lifecycle events and research marts remain later stages.

This stage consumes the existing deployment bundle without changing its schema, normalizer or saved outputs. It adds three arrays in a separate, immutable replay artifact:

| Array | Grain and meaning |
| --- | --- |
| `identity_candidates` | One proposed relation with its observation, target HF repository, optional canonical reference, method, priority and evidence. A candidate is not an accepted link. |
| `identity_decisions` | One provider deployment's identity belief over an observed-time interval. States are `resolved`, `ambiguous` and `unresolved`. Includes selected and newly considered candidate IDs. |
| `identity_links` | Resolved decisions only, with stable link IDs and the same intervals/provenance. A linked HF repo is not promoted into a canonical model. |

Deployment keys remain exact `(provider, provider_model_id)` hashes. HF repository strings are preserved with case and variants intact. Provider ID prefixes, names, slugs and apparent model families do not establish canonical identity.

## Evidence policy

| Priority | Method | Initial operational policy |
| --- | --- | --- |
| 1 | `explicit_provider_mapping` | Automatically extracted from OpenRouter `hugging_face_id` or the parent HF model `id` for an explicit provider mapping. Must be a qualified `owner/repo` string with an identified deployment and known capture time. |
| 2 | `exact_external_identifier` | Accepted through an evidence ledger after review. The ledger records a reference and rationale; this stage does not claim to independently verify an external registry. |
| 3 | `exact_deterministic_correspondence` | Same review gate, with an explicit evidence reference. No correspondence heuristics are generated here. |
| 4 | `curated` | Explicit review with reviewer and reason. May reference a canonical entity; it does not create a canonical entity or registry. |
| 5 | `fuzzy` | Candidate for review only, even if marked approved. Cannot produce an identity link or canonical assignment. Manual acceptance must be recorded as separate curated evidence. No fuzzy matching algorithm runs in this stage. |

Pending/rejected supplementary candidates cannot create links. Missing, malformed or date-only source identity evidence remains available for review without inventing a timestamp or mapping. Canonical references on exact/fuzzy source matching are never inferred. Non-curated accepted methods cannot carry canonical references. Canonical assignments require approved curated evidence matching the selected HF repo; conflicting curated canonical references leave that field null with `canonical_status=ambiguous`.

For each method, the latest eligible evidence batch replaces its earlier batch. The strongest available method determines the relation. A later lower-priority claim cannot silently replace an earlier explicit source mapping. Missing provider rows or missing HF declarations do not retract existing identity evidence. Historical links are beliefs based on retained evidence, not claims that a deployment is still present or healthy. Rejecting a new candidate is not revocation of an already accepted link; explicit revocation is not implemented in this version.

## Time and conflicts

Candidates retain the exact observation timestamp and precision. Rows for the same deployment and source snapshot are evaluated together. The decision becomes available at the latest of their capture timestamps. Thus two HF pages declaring different GLM variants cannot be interpreted as an ordered correction. Equal-time batches are combined, so input row order cannot choose the winner.

Distinct highest-priority targets produce `ambiguous`, close the preceding accepted interval and create no replacement link. Lower-priority evidence cannot break that tie. A later unique highest-priority mapping can resolve it. Repeated snapshots produce separate intervals so each decision's evidence stays explicit.

`valid_from`/`valid_to` are half-open observed-belief intervals `[from, to)`, expressed in UTC. They are not source-effective dates. `effective_at` remains null. A batch containing an unknown capture instant creates no precisely timed decision; its candidates stay in the output. `recorded_at` is the actual materialization time, and historical replay is marked `is_backfill=true`.

`identity-at --at T` answers the replay's identity belief using evidence available by T. `--recorded-as-of R` additionally refuses to expose a replay recorded after R (`not_recorded`). This does not pretend the migrated identity pipeline existed at the historical capture date. Replays are separate files; corrections never overwrite a previous artifact. The query operates on one explicitly selected replay, not an automatically merged transaction history across runs.

## Input integrity and publication

Before identity extraction, the CLI reconstructs **all observations through each input source cutoff** from the protected raw archive. It compares complete source facts, IDs, row hashes, locators, timing and provenance, rejecting edited observations and omitted rows. It reads later archive snapshots for integrity checks but does not add them to an older deployment bundle's identity history.

Old manifest evidence hashes may match only their frozen bytes or an attested LF/CRLF representation; compressed payloads and decompressed payload hashes remain exact. The whole current archive, including additions since baseline, is fingerprinted before/after. Input deployment/ledger file hashes are pinned and checked before publication. Outputs are exclusively created under approved derived/validation directories. `--dry-run` validates everything and writes nothing.

Candidate, decision and link IDs exclude materialization time. The artifact pins semantic hashes of its deployment bundle and evidence ledger, plus input stored-file hashes. Candidate IDs point to original observation IDs and retain complete source evidence or reviewed ledger entries.

```powershell
python -m temporal identity --observations data/temporal_v2/deployment_layer_2026-10-03.json --output data/temporal_v2/identity_history_2026-10-03.json
python -m temporal identity --observations data/temporal_v2/deployment_layer_2026-10-03.json --evidence data/validation/identity_review_2026-10-04.json --output data/temporal_v2/identity_history_2026-10-04.json
python -m temporal identity-at --history data/temporal_v2/identity_history_2026-10-03.json --deployment-key <exact-deployment-key> --at 2026-10-02T12:00:00Z
```

The optional ledger schema is intentionally small:

```json
{
  "schema_version": 1,
  "candidates": [{
    "observation_id": "<existing-source-observation-id>",
    "mapping_method": "curated",
    "hf_repo_id": "owner/exact-repository",
    "canonical_model_id": null,
    "evidence_available_at": "2026-10-04T12:00:00Z",
    "evidence_reference": "<review-or-source-reference>",
    "review_status": "approved",
    "reviewed_by": "<reviewer>",
    "review_reason": "<specific supporting evidence>"
  }]
}
```

Evidence cannot predate its source observation or arrive after materialization. The ledger is an explicit local review record, not authenticated proof that a particular person performed the review. Review files belong in version control; derived replay bundles remain ignored by Git.

## Recorded validation

The existing deployment bundle's source cutoff is **2026-10-02**, despite its materialization filename. Its saved identity replay contains 19,683 explicit candidates, 28,953 decisions and 19,671 resolved link intervals. There are 9,276 unresolved and **6 ambiguous historical decisions**. Latest decisions across 7,091 deployments are 6,784 resolved, 303 unresolved and 4 ambiguous. Canonical assignments and events: **0**.

The six ambiguous decisions preserve the observed `zai-org` collisions: `glm-5.2` and `glm-5.3` on October 1 and 2, plus `glm-4.6` and `glm-5.1` on October 2. Standard, FP8 and BF16 HF repositories remain separate candidates. No row-order tie break is applied.

All 108 tests passed locally and in an isolated copy with protected manifests converted to LF. Replaying the original CRLF-attested deployment bundle against that LF copy produced identical candidates, decisions and links. This is a simulated LF checkout on Windows, not an executed Linux CI job. Ruff passed for the temporal implementation and tests. Validation evidence is recorded in `data/validation/identity_history_validation_2026-10-03.json`.
