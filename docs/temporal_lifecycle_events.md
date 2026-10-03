# Phase 5 — deterministic lifecycle event engine v1

The engine consumes the existing deployment facts and a validated temporal identity replay. It creates a new derived JSON bundle under `data/temporal_v2`, with no source rewrite, consumer cutover or additional service. Source cutoff follows the input bundle, not the filename or execution date.

## Enabled and deferred event types

| Enabled type | Precise v1 meaning |
| --- | --- |
| `PROVIDER_LISTED` | First positive observed presence of an exact provider deployment in the supplied history. Always left-censored: this does not prove a launch/listing date. Emitted once per deployment, regardless of later catalogue gaps. |
| `PRICE_CHANGED` | A comparable source pricing value changed between positive observation batches within the same deployment, source and scope. No canonical identity is required. |
| `CONTEXT_CHANGED` | A comparable, explicitly known context value changed between those batches. |
| `CAPABILITY_CHANGED` | A comparable, explicitly known capability declaration changed between those batches. |
| `HF_IDENTITY_LINKED` | The first resolved HF repository relation in this identity replay. An initial ambiguous/unresolved candidate is not a linked identity. |
| `HF_IDENTITY_REVISED` | After a first accepted HF relation, the HF belief changed: repo revision, transition to ambiguity, changed ambiguous target set, or subsequent resolution. Repeated evidence for the same belief produces no revision. |

| Deferred type | Reason |
| --- | --- |
| `PROVIDER_DELISTED` | Requires approved source-specific absence evidence policy. |
| `MODEL_REAPPEARED` | Requires reliable prior confirmed delisting. |
| `PROVIDER_ID_CHANGED` | Requires proven continuity between distinct deployment IDs. |
| `MODEL_DISCOVERED` | The meaning of the model entity remains undecided when canonical identity is null. |

Both lists and reasons are serialized in every bundle. There is no absence event path in v1. Missing deployments, failed/partial catalogues and gaps create no events. If a deployment later returns, it is not listed again and is not labelled reappeared. A proved value difference across two available positive observations may still produce a change event: its timing is bounded by those observations, not assigned to an unobserved day inside the gap.

Canonical identity is optional event metadata. Property changes use deployment facts directly. HF events use accepted identity decisions, not similarity matching. A canonical-only review with an unchanged HF belief is outside these six types and emits no HF revision.

## Comparison semantics

The property comparison series is `(deployment_key, source, source_scope)`. A comparison batch contains all rows for that deployment and source snapshot. Rows across equal capture instants are combined rather than ordered by row number. A batch becomes available at the latest capture of its rows. This protects against treating conflicting GLM rows on different HF pages as an ordered change.

Each of price, context and capabilities has its own previous comparable batch. Conflicting normalized declarations reset that field's anchor and are counted in diagnostics. Missing/all-unknown values also reset the anchor. Other independently comparable fields can still produce events. Equal duplicate declarations preserve all row evidence without duplicating events.

Only paths explicitly known in **both** batches are compared. A new nullable field, disappearance of an optional key, or null value does not establish a value transition. After a newly known value is observed, it can serve as the baseline for later comparisons. `changed_paths` records exactly which comparable paths differ; raw `previous_value` and `new_value` retain the complete source objects, including their nullable fields.

Pricing uses exact finite Decimal comparison for numeric strings/numbers, without float arithmetic or currency/unit conversion. `0.10`, `0.1` and numeric `0.1` compare equally; zero and scientific notation are normalized consistently. Source keys remain distinct, raw values remain in evidence, and comparison requires matching declared currency/unit/source field. Unknown units are not guessed: this is a change in the same feed's declared pricing value, not a cross-provider cost estimate. Non-numeric pricing labels remain typed values.

Context and capability leaf values use typed JSON comparisons; false is not numeric zero. Capability arrays are interpreted as lists of supported declarations and compared without ordering significance. Empty arrays and false values remain known; absent/null paths remain unknown.

A date-only first presence may produce `PROVIDER_LISTED` with `observed_at=null`, its original day and day precision. It is never assigned midnight or an identity decision whose ordering within that day is unknown. Date-only property batches block comparisons across that entire day in their series. An undated batch blocks that series' property comparisons; an undated positive presence prevents asserting a first dated listing. Diagnostics expose these exclusions.

## Time, identity and provenance

`observed_at` is the later comparison batch capture, or an identity decision's `valid_from`; UTC instants preserve evidence precision separately. `previous_observed_at` bounds the earlier comparison/decision. `effective_at` remains null because source capture cannot prove when a price, deployment or identity mapping actually changed. Historical replay is marked `is_backfill=true`. `recorded_at` is this event materialization's actual time, which cannot precede its identity input.

Property/presence events attach the identity decision available **as of their event observation time**, if one exists. An ambiguous decision yields null HF/canonical metadata. Identity events carry current/previous decision IDs and selected candidate IDs; those resolve to source declarations or reviewed evidence in the pinned identity input. No future resolved identity is used to relabel an earlier event.

Every event contains its exact provider/ID, deployment key, ordered observation IDs, current/previous snapshot IDs, source paths/hashes/row locators and original capture times/precision. `event_id` hashes algorithm version, type, deployment, field/changed paths, old/new typed objects, ordered observation evidence, observed time/day and identity decision references for HF transitions. Materialization timestamps are excluded. Full event payloads and IDs reproduce for the same inputs; IDs also survive a later execution time. Source/identity input hashes remain separately pinned.

The CLI validates raw preservation, reconstructs deployment observations through the input cutoffs, and rebuilds the identity tables with the original review ledger and identity recording time. Altered or unrelated identity history is rejected. It checks the full current archive and all input file hashes before/after replay, including archive additions since baseline. Publication exclusively creates a new file after successful checks. Existing artifacts are refused, and dry-run writes nothing. The original baseline, deployment bundle, identity bundle and v1 outputs remain intact.

```powershell
python -m temporal events --observations data/temporal_v2/deployment_layer_2026-10-03.json --identity data/temporal_v2/identity_history_2026-10-03.json --dry-run
python -m temporal events --observations data/temporal_v2/deployment_layer_2026-10-03.json --identity data/temporal_v2/identity_history_2026-10-03.json --output data/temporal_v2/lifecycle_events_2026-10-03.json
```

If the selected identity replay used a supplementary ledger, provide that same ledger with `--evidence`; its semantic hash and rebuilt tables must match. The engine does not generate or approve new identity candidates.

## Validation against the saved history

The initial materialization uses the existing deployment and identity bundles with source cutoff **2026-10-02**:

| Type | Count |
| --- | ---: |
| `PROVIDER_LISTED` | 7,091 |
| `PRICE_CHANGED` | 440 |
| `CONTEXT_CHANGED` | 242 |
| `CAPABILITY_CHANGED` | 166 |
| `HF_IDENTITY_LINKED` | 6,786 |
| `HF_IDENTITY_REVISED` | 2 |
| **Total** | **14,727** |

The two revisions are prior accepted `zai-org/glm-4.6` and `zai-org/glm-5.1` mappings becoming ambiguous after FP8 declarations. The other GLM collisions are initially ambiguous, so they do not invent an initial HF link. Deferred/absence event count is zero. Unknown comparison values are explicitly counted rather than treated as zero/false; the historical corpus contains no precisely timed property-comparison conflicts, despite identity conflicts.

Tests cover deterministic IDs/order, identical history, optional canonical identity, known value changes, Decimal representations, capability ordering/typed booleans, source/scope isolation, missing evidence, duplicate/conflicting declarations, date-only capture barriers, identity ambiguity and resolution, canonical-only/fuzzy review, input reconstruction, dry-run/output guards and racing archive/input mutation. Validation records Windows/LF simulation results, input/output and implementation hashes, replay equivalence, real event counts and frozen archive preservation in `data/validation/lifecycle_events_validation_2026-10-03.json`. LF tests simulate checkout representations on Windows; Linux CI itself is not claimed to have run.

The final full suite passed **134 tests** in both the working tree and the isolated LF copy; Ruff passed. The LF replay reproduced all 14,727 event payloads and IDs, and a later materialization reproduced the same real event IDs.
