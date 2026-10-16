# Independent public acceptance coverage

Frozen from CONTRACT.md alone, without candidate code, implementation reasoning,
private findings, sibling workspaces, network access, or delegated agents.
Run from the candidate directory with `python tests/test_acceptance.py`.
Only Python's standard library is required. No execution against a candidate
has occurred; no passing result is claimed.

The suite covers exact case-preserved repository IDs and distinct variants;
source precedence across all three legs; identical repeat collapse; conflicting
equal-precedence rejection; chronological canonical ordering; canonicalization
inside features and intervals; independent non-null metric endpoints and their
dates; all-null, singleton, and zero download baselines; declining likes;
coverage and maximum gap geometry; adjacent intervals without bridging nulls;
actual UTC instants with an offset and irregular fetch spacing; explicit date
fallback; and zero or negative elapsed-time rejection.

Eligibility fixtures isolate span 27 versus 28, coverage exactly .8 versus
23/30, maximum gap three versus four, and 21 versus 20 non-null downloads.
Missing downloads still count as observed dates. The rolling-window example
loses 400 expiring downloads while gaining 100 new downloads, so the observed
30-day count decreases by 300 over 1.5 days. The expected -200 rate describes
window-count change, despite positive actual downloads; it cannot estimate
actual daily downloads. Numerical expectations are independently fixed from
fixture arithmetic, not copied from a candidate.

The CLI test creates a unique temporary fixture and two output directories
under the candidate's scratch/ only when executed later. It checks all seven
required artifacts, JSON parseability, basic CSV counts and exact IDs, missing
CSV metric handling, interval arithmetic, case observation membership and
download values, nonempty report/audit prose, unchanged fixture bytes, and
byte-for-byte deterministic reruns. Temporary content is removed afterward;
scratch/ itself may remain. It neither edits candidate code nor uses or changes
authoritative data. The fixture supplies measured growth and decline histories.

Contract details without fixed spelling are checked semantically: metric date
fields must contain the metric name, first/last, and date; fallback metadata may
be a true fallback flag or a descriptive date-based basis/source/method. Errors
are expected to raise ValueError. These conventional interface assumptions may
need adjudication if a conforming implementation uses other names or exceptions.
No global ordering across repository groups is assumed. Multi-repository input
to single-repository features, empty histories, malformed input, conflicting
lower-priority rows beneath a winner, and optional extra fields are unspecified
and deliberately not prescribed.

This compact suite is not a full research review. It does not validate real
Silver parsing, raw extraction/cutoff reconciliation, authoritative data audits,
selection bias analysis, sensitivity prose, scientific claim quality, thresholds
for case classes, charts, causal wording, or predictive leakage. JSON and case
schemas are unspecified: the suite checks basic artifact consistency but cannot
fully reconcile narrative numbers, case identities beyond repository membership,
or require a particular selection algorithm. Empty case tables can pass that
consistency check; evidence-backed case sufficiency requires independent review.
Byte determinism intentionally rejects timestamps or output-path-dependent text
in required artifacts. CLI execution has a 60-second timeout for this tiny input.
