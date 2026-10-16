# Research validation and reproducibility

The original research validation was completed on 2026-10-14. The table below
records those results; publication checks are recorded separately in
[validation.json](evidence/validation.json).

| Check | Original observed result |
|---|---|
| Frozen acceptance suite | 9 unittest tests passed |
| Project regressions at the research baseline | 134 pytest tests passed |
| Project lint | Ruff passed |
| Silver reconciliation | All 237,930 overlapping rows matched |
| Source-to-artifact reconciliation | 322,661 observations, 32,986 repositories, 289,675 intervals and 186 case observations reconciled |
| Windows reproduction | All 11 result files, including four figures, byte-identical |
| Linux reproduction | All seven core result files byte-identical to Windows |
| Source preservation | All 320 protected raw/Silver/Gold files unchanged |

## Automated independent review

An automated independent read-only review accepted the research with no blockers.
It recomputed the reported counts, weekly medians, peaks, variability, reversals,
endpoint arithmetic and sensitivity screens from the supplied extract. Tables,
reports and selected-case figures agreed. This was automated review, not a claim
of human scientific approval.

The review did not independently rerun extraction, full artifact reproduction or
the file-writing acceptance suite; those claims rely on recorded executions and
provenance. Short follow-up, bounded discovery sampling, retrospective eligibility
and rolling download counts remain scientific limitations. Validation does not
establish lifecycle classes, population prevalence, causality or prediction.

## Reproduce the checks

Follow [README.md](README.md) to regenerate the input and result tables, then run
from this research directory:

```powershell
New-Item -ItemType Directory -Force scratch | Out-Null
python -B evidence/frozen-v1/tests/test_acceptance.py
python -B validate_artifacts.py --input input/observations.csv --results results --receipt reproduction/check.json
```

The acceptance test source is preserved unchanged. Its original
[coverage note](evidence/frozen-v1/tests/COVERAGE.md) describes scope and limits;
its pre-execution wording is archival. The suite checks identity, precedence,
ordering, null/zero baselines, irregular UTC intervals, eligibility boundaries,
artifact consistency and deterministic reruns. It does not replace source-data,
figure or scientific-method review.

Technical evidence includes [reproduction](evidence/reproducibility.json),
[arithmetic reconciliation](evidence/artifact-reconciliation.json),
[Silver overlap](evidence/silver-reconciliation.json),
[interval checks](evidence/interval-preflight.json) and
[Linux core byte comparisons](evidence/linux-byte-comparison.json).

## Runtime and limits

Original Windows extraction and reconciliation used Python 3.13.1 and pyarrow
25.0.1; Linux project tests used Python 3.14.4, pytest 9.0.2 and pyarrow 24.0.0.
The nine frozen tests use standard-library unittest. The original Windows lint
runtime was Ruff 0.16.5; an earlier report mistakenly named the requirements-file
pin 0.6.9. These are observed runtimes, not a claim of recreating that dependency
pin. [Runtime metadata](evidence/runtime.json) preserves the measured versions.

Matplotlib 3.10.7 and plotting dependencies are pinned in
[requirements-plot.txt](requirements-plot.txt). Figure byte agreement was checked
on the same Windows runtime and fonts; it is not a cross-platform rendering
guarantee. For plotting in a restricted shell:

```powershell
New-Item -ItemType Directory -Force reproduction/matplotlib-cache | Out-Null
$env:MPLCONFIGDIR = Join-Path (Get-Location) 'reproduction/matplotlib-cache'
```

Checks caught repeated rounded tick labels, lint issues and CRLF/LF output
differences; integer ticks, lint corrections and explicit LF serialization
resolved them. The frozen test source and numerical results were preserved.
No scientific calculation defect was found by the independent review.

The input manifest pins source membership and hashes. Regenerating exact audit
metadata requires those inputs. No historical data, ingestion, identity mappings,
cloud resources or live collection were modified for publication.
