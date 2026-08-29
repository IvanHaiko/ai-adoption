# ai-adoption — collector

Daily raw snapshots of what the AI industry actually hosts and serves, kept so
that a usage time series can exist at all.

**Why this runs before anything else is designed.** arXiv hands out its full
history on request. Usage does not: HuggingFace's `downloads` is a rolling
30-day figure with no history in the API, and OpenRouter publishes no usage
history in any form — its rankings page carries two dates. So the usage series
starts on the day collection starts, and **a missed day cannot be
reconstructed**. The schema, the warehouse and the DAG can be written whenever;
this cannot.

## What it collects, daily

| Leg | Source | Stored as |
|---|---|---|
| `openrouter_models` | `https://openrouter.ai/api/v1/models` | `openrouter_models.json.gz` |
| `openrouter_rankings` | `https://openrouter.ai/rankings` | `openrouter_rankings.html.gz` |
| `hf_models` | `https://huggingface.co/api/models/{id}`, one call per repository | `hf_models.jsonl.gz` |
| `hf_top_models` | `https://huggingface.co/api/models`, the top 5 000 text-generation repositories by downloads, 5 cursor pages | `hf_top_models.jsonl.gz` |

No credentials. All three endpoints answer anonymously — verified 2026-08-29
with no key present. `.env.example` explains the one case that would need a key.

## Measured, not assumed

Every number below came from a live request on **2026-08-29**; re-measure
before quoting it.

- OpenRouter lists **398 models**.
- **180 of 398 (45%)** carry a non-empty `hugging_face_id`. The field is
  *present on all 398* and an empty string on 218 of them — testing for
  presence instead of for a value overstates the link to 100%.
- Those 180 point at **152 distinct repositories**; several models share one.
- **151 of 152 resolve.** `microsoft/WizardLM-2-8x22B` answers `401` — gated or
  withdrawn. It is recorded in the manifest as a failure and never as a zero.
- A day costs **1 012 KB** on disk gzipped. A year of daily snapshots is
  ~**370 MB**; the ~110 days to the end of the capstone, ~**111 MB**.
- A full collection takes **~41 s** and **158 HTTP calls**. A re-run of a
  complete day takes **0.4 s** and **0 calls**.
- The ranking leg returns **5 000 rows over 5 pages**, 491 KB of the day.
  **35%** of those rows carry an `arxiv:` tag and **64%** a `base_model:` tag.

So the OpenRouter ↔ HuggingFace join is **key-based, not fuzzy — for the 45% of
the catalogue that declares a key.** The other 55% is the project's real
coverage problem, and it belongs in the README of the finished pipeline too.

## Why the ranking leg exists, and why the top is 5 000

OpenRouter's rankings page carries a top-ten leaderboard - measured on
2026-08-28, its embedded payload held **20 records across two dates**, about
ten models a day out of a 398-model catalogue. That is not enough to measure
adoption, so the ranking comes from HuggingFace instead.

`TOP_N` is set from the download distribution, measured 2026-08-29 by paging
34 000 repositories:

| top N | share of all downloads in the category | downloads at rank N |
|---|---|---|
| 100 | 60.3% | 979 293 |
| 1 000 | 92.4% | 36 738 |
| 2 000 | 95.6% | 9 351 |
| **5 000** | **97.9%** | **1 822** |
| 10 000 | 98.9% | 635 |

Two things to know before computing a share from this leg.

`downloads` is a rolling 30-day figure, not a daily increment. Differencing it
gives a noisy pseudo-daily series; the shape of a release curve survives that,
a precise daily count does not.

**Rank counts repositories, not models.** Much of the tail is quantisations and
re-uploads of the same weights - `unsloth/...`, `...-GGUF`, `mradermacher/...`
- so a model's real adoption is spread across derivatives and the head
understates it. 64% of the 5 000 declare a `base_model:` tag, which is what
makes rolling them back up to a canonical model possible. That roll-up, with
its coverage published, is the leg's reason for reaching past the head.

## Layout

```
data/raw/<YYYY-MM-DD>/          # the UTC date is the day key
  _manifest.json                # what was asked for and what came back
  openrouter_models.json.gz
  openrouter_rankings.html.gz
  hf_models.jsonl.gz            # one line per repository, not 152 files
  hf_top_models.jsonl.gz        # one line per cursor page, not 5 000 files
```

The manifest is what makes the directory self-describing: per leg, the URL,
HTTP status, byte counts, and the SHA-256 of the bytes as received. For the
HuggingFace leg it also carries `requested`, `stored` and the list of
`failures`. A day is `complete` only when every leg is `ok`.

## Bronze rules this enforces

1. **Nothing is reformed.** The OpenRouter payload is stored byte-for-byte. Each
   HuggingFace line is an envelope (`hf_id`, `fetched_at`, `http_status`)
   concatenated around the response bytes — the body is never re-serialised.
   `tests/test_bronze_is_verbatim.py` checks both.
2. **Absence is not zero.** A repository that returns 401/404 produces no row.
   It produces a manifest entry.
3. **Idempotent by day key, and the double run proves it.**
   `tests/test_idempotence.py` runs the collector twice and asserts the second
   run made zero network calls and changed zero bytes — including the manifest.
4. **Polite.** Identifying User-Agent with a contact address, a floor between
   requests, bounded retries, `Retry-After` honoured. Never a workaround.

## Running it

```bash
pip install -r requirements-dev.txt
python -m collector                 # today, UTC
python -m collector --date 2026-08-28
pytest -q && ruff check .
```

Exit code is `0` for a complete day and `1` for a partial one, so the scheduler
can tell "collected" from "collected some of it".

## Scheduling

`.github/workflows/collect.yml` runs at 06:20 and 18:20 UTC. The second run of
a complete day is a no-op costing zero HTTP calls, so the redundancy buys
tolerance for one delayed or dropped schedule at almost no cost.

**Known operational risk:** GitHub disables scheduled workflows after 60 days
without repository activity, and a push made by the workflow's own token is not
a reliable substitute for a human one. Verify the schedule is still enabled
monthly. A silently stopped collector is the one failure this project cannot
recover from.

## Second copy, and knowing the collector is alive

GitHub holds the first copy. A local clone is the second, with full history and
no credentials — the snapshots are ordinary files in `data/raw/`, so nothing has
to be unpacked.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\pull_snapshots.ps1
```

It fast-forwards the clone and then reports what is actually there: how many
snapshot days, how far the latest one is behind UTC today, whether any day is
missing from the middle of the range, and whether any day never reached
`complete`. Exit code `1` on any of those.

Missing a pull costs nothing, which is why this one can live on a laptop that
sleeps. Its real value is the check: a stopped collector is silent — the
workflow simply does not run and nothing sends a failure — so running this daily
caps how long that can go unnoticed at one day.

## Auditing what is on disk

```bash
python -m collector.audit
```

The collector cannot see three of its own failure modes, so a separate pass
looks at the tree from outside. It runs in the `collect` workflow after the
commit, in CI, and from `pull_snapshots.ps1`.

| Check | Catches |
|---|---|
| `staleness` | the collector stopped and nothing said so |
| `gap` | a day absent from the middle of the range |
| `incomplete` | a day that never reached `complete` |
| `integrity` | stored bytes that no longer match the SHA-256 the manifest recorded |
| `coverage` | a day that is `complete` and still thin |
| `catalogue` | OpenRouter listing far fewer repositories than yesterday |
| `ranking` | the ranking leg succeeding and still returning a fraction of its rows |

`coverage` is the one worth explaining. A day can pass every other check and
still be nearly empty: if HuggingFace begins rate-limiting halfway through,
every individual response is a legitimate answer, the manifest is internally
consistent, and the day is honestly marked `complete`. Nothing inside the
collector can tell. Only yesterday's ratio shows it — 30 of 152 against 151 of
152 the day before. The thresholds (5% warn, 20% error) are **chosen, not
measured**; there is one day of history so far.

`gap` and `staleness` are deliberately separate. The distance from the last
snapshot to today is lag; holes are what is missing between the first snapshot
and the last. Counting the trailing distance as holes would report the same
absent days twice under two names.

### Acknowledged gaps

A missed day cannot be collected later, so without a way to admit one the audit
would stay red forever — and an alert that is always red is not read by the
time the next gap appears. `known_gaps.txt` takes one date per line with a
reason; those days are reported as warnings, with the reason, instead of
errors. Acknowledging is not repairing. Never add a date to silence a gap that
has not been understood.

## Partial days

A transient failure (timeout, 5xx, 429) leaves the day `partial` and the next
run picks up exactly what is missing — it does not re-fetch what already
landed. `tests/test_resume.py` covers both halves.
