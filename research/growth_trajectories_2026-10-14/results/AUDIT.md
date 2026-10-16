# Input and computation audit

SHA-256: `235b05b25221b3f102cb8d5bd392c9f1bf96df5d629bf667b148790c8910317d`. Manifest hash match: True; row count match: True.

Raw/canonical rows: 322,661/322,661; collapsed or precedence-discarded rows: 0. Equal-precedence ambiguous duplicates raise an error. Exact case-preserved identities only.

Unique/repeated repositories: 32,986/31,062. Observation-count distribution: {1: 1924, 2: 2467, 3: 7416, 4: 11468, 5: 2644, 6: 197, 7: 133, 8: 125, 9: 104, 10: 93, 11: 118, 12: 67, 13: 158, 14: 112, 15: 126, 16: 116, 17: 97, 18: 119, 19: 109, 20: 107, 21: 101, 22: 95, 23: 92, 24: 85, 25: 159, 26: 85, 27: 111, 28: 110, 29: 177, 30: 116, 31: 99, 32: 70, 33: 105, 34: 70, 35: 63, 36: 65, 37: 43, 38: 55, 39: 53, 40: 66, 41: 46, 42: 58, 43: 80, 44: 50, 45: 93, 46: 2997, 47: 142}.

Missing values: {'downloads_30d': 0, 'likes': 0, 'fetched_at': 0, 'created_at': 0, 'pipeline_tag': 752}. Source legs: {'hf_models': 7221, 'hf_new_models': 89387, 'hf_top_models': 226053}.

Intervals: 289,675; time bases: {'fetched_at_utc': 289675}; date-spacing distribution: {1: 288160, 2: 331, 3: 186, 4: 146, 5: 124, 6: 86, 7: 68, 8: 57, 9: 44, 10: 39, 11: 40, 12: 29, 13: 27, 14: 30, 15: 23, 16: 33, 17: 32, 18: 18, 19: 29, 20: 12, 21: 7, 22: 19, 23: 16, 24: 22, 25: 14, 26: 11, 27: 8, 28: 7, 29: 6, 30: 2, 31: 8, 32: 2, 33: 9, 34: 3, 35: 3, 36: 5, 37: 2, 38: 7, 39: 5, 40: 3, 41: 1, 42: 1}. Nonpositive elapsed UTC time raises an error. Missing metrics invalidate only their adjacent changes; no bridging, zero filling or infinite relative rates.

Existing Silver DQ coverage: ['2026-08-28', '2026-10-01']; metadata row counters: {'hf_observations': 241378, 'hf_repositories': 237930, 'openrouter_models': 15326, 'arxiv_papers': 20422, 'arxiv_id_resolutions': 1194, 'model_platform_links': 203, 'hf_arxiv_links': 10657, 'hf_repo_cohorts': 25800, 'hf_provider_model_observations': 6437, 'hf_inference_provider_observations': 6583}. Existing Gold cutoff: 2026-10-01; research overlap rows: 237930; extension rows: 84731.

The supplied research context independently checked all 237930 canonical Silver overlap rows through October 1 against this raw-derived extract. This is external evidence, not a check rerun here. The Silver DQ hf_observations counter includes a different row representation; do not equate it with the canonical research overlap. Gold primary cohorts and milestone denominators differ from these descriptive screens.

Manifest parser errors: []; protected files unchanged (manifest assertion): True. Source hashes: 320; parser SHA-256: f0aa2e0249f3db092e90ac1e025294cd5099d9b8306e38eb243a79f9648eb572.

Selection bias: leg availability varies by date, top-ranked sampling is outcome-dependent, new-model histories are short and exits can be sampling exits. Within-span coverage can be 100% for a singleton; it does not imply long follow-up. Eligibility uses the full retrospective history and must not be used as an early predictive feature.

Eligibility denominator: all 32,986 exact repositories; baseline 4,172, strict 3,286. Shape sensitivity: {'baseline': {'sustained_growth': 253, 'sustained_decline': 400, 'material_interior_peak': 322}, 'stronger_shape': {'sustained_growth': 73, 'sustained_decline': 140, 'material_interior_peak': 35}, 'strict_histories': {'sustained_growth': 195, 'sustained_decline': 361, 'material_interior_peak': 269}}.

Reproduction is deterministic: sorted IDs/dates, fixed thresholds and tie breaking, no random seed or wall-clock metadata. Independent testing and plot execution are supplied by the parent; no acceptance result is asserted.
