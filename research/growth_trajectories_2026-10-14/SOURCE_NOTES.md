# Source semantics checked on 2026-10-14

The official [HfApi ModelInfo documentation](https://huggingface.co/docs/huggingface_hub/main/package_reference/hf_api#huggingface_hub.ModelInfo)
defines `downloads` as the last-30-day count and separately defines
`downloads_all_time` as cumulative downloads. This project's historical parser
preserves only the former as `downloads_30d`; the latter is not reconstructed.
The official [download counting documentation](https://huggingface.co/docs/hub/models-download-stats)
describes file-request counting rules that depend on model/library formats.
Counts are not unique people or demonstrated production deployments.

For a conceptual daily rolling sum R(t), its one-day difference equals entering
counts minus counts leaving the window, not daily downloads. Irregular fetch
times and possible source revisions further limit this interpretation. We
analyze measured rolling-count changes and their rates, not inferred daily flow.

Local identity and sampling contracts: `README.md`,
`docs/gold_metric_contract.md`, and `docs/temporal_identity_history.md`.
Exact HF IDs preserve repository variants, quantizations and case. Gold's
primary cohort and milestone semantics differ from this descriptive trajectory
screen; do not mix denominators. Initial creation timestamps do not establish
unobserved early popularity histories.
