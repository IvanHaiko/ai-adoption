# Reproduction

Run these commands from the delivered research directory root, where
`analysis.py`, `plot.py`, `README.md`, `prepare_input.py`, and
`requirements-plot.txt` reside. For a new checkout, create the plotting runtime
and install the pinned research plotting dependencies:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-plot.txt
```

The existing environment is already provisioned; skip setup there. Extract with
the existing project Python (which has pyarrow), run the standard-library core
analysis with that Python, then plot with the research plotting runtime:

```powershell
python -B prepare_input.py --output input --cutoff 2026-10-13
python -B analysis.py --input input/observations.csv --output results
.venv/Scripts/python.exe -B plot.py --results results
```

`prepare_input.py` already exists in that directory and regenerates the input
CSV and real `input/input_manifest.json` from immutable Bronze using the existing
Silver parser and source precedence. The real manifest is required to reproduce source audit metadata;
keep it beside `observations.csv`. Preserve the pinned source hashes and inspect
the generated manifest when regenerating.

Core analysis is standard-library only. The exported functions are
`canonicalize`, `repository_features`, and `interval_changes`; they accept row
dictionaries, including integer/None metrics. Exact case-preserved repository
IDs are never grouped into families. Empty CSV metrics become null; CSV nulls
are blank, JSON nulls are null. Ambiguous same-source duplicates and nonpositive
elapsed time are errors. Offset-aware timestamps are converted to UTC; absent
fetch timestamps use explicitly marked date-spacing fallback.

The analysis produces exactly seven files: `summary.json`,
`repository_features.csv`, `interval_changes.csv`, `cases.csv`,
`case_observations.csv`, `REPORT.md`, and `AUDIT.md`. The report contains measured
findings, thresholds, sensitivity, deterministic selection rules and limitations.
The audit records input digest, manifest reconciliation, missingness, observation
and interval distributions, and validation provenance. No findings or case IDs
are hardcoded. Rows are sorted; no random choices or current-time metadata enter
outputs. Empty eligible screens are reported honestly.

The plot command reads the generated tables and writes only
`results/plots/representative_cases.png`, `representative_cases.svg`,
`coverage.png`, and `coverage.svg`. Individual panels have independent scales.
Absent dates and missing metrics create NaN gaps, with measured points visible;
no interpolation or extrapolation occurs. SVG hashsalt is fixed and date metadata
removed. Byte determinism requires the same pinned matplotlib, fonts and runtime.
See [VALIDATION.md](VALIDATION.md) for observed test results, automated review
limitations and the runtime used for figure reproduction. The frozen acceptance
suite is retained under `evidence/frozen-v1/tests/`.

47 days of overlapping rolling counts support descriptive short-window patterns,
not established lifecycle classes or predictive reliability. Rolling-count change
per day is not actual daily downloads; likes are observed stock counts. The
selected sample and retrospective eligibility are unsuitable for claims about
population prevalence or early prediction without a separate study.
