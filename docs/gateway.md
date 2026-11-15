# Gateway usage archive

## Regular core operation

The core sources are Hugging Face (existing daily Bronze collector), OpenRouter
and Vercel. Cloudflare Radar and Replicate are separate experiments and are not
called by the scheduled workflow.

`python -m gateway.pipeline` refreshes a seven-day window ending on the last
completed UTC day. `--window-days 30 --end 2026-11-14` requests an explicit
completed interval. Each gateway is attempted independently; successful raw
responses survive failure of the other. A missing key, fetch/build error or
missing selected day/metric makes the run fail visibly. No inference is run.

`python -m gateway.pipeline --offline` builds from existing responses without
network access; use `--end` to select the archive cutoff when inspecting an old
checkout. The report is `data/analytics/core_report.md` with machine-readable
JSON beside it. A curation queue prioritizes high-volume unresolved named models.
Token counts and percentages retain different units and source denominators.

The existing collect workflow runs this command with the OpenRouter secret
available only to that step, then commits only the core source archives and
daily Bronze through the existing publication mechanism. Source errors do not
prevent preservation of another successful source. Derived reports are rebuilt
from committed bytes and the Markdown report is appended to the Actions summary.
This local workflow change becomes active after the user publishes it.

OpenRouter exact permaslug links are resolved against that calendar day's raw
catalogue and its nonempty HF declaration; missing days are never filled using
future links. Conflicting declarations stay ambiguous. Vercel continues to use
the reviewed identity ledger because its usage export has names, not HF IDs.
Curated links conflicting with source declarations also remain ambiguous.
Every accepted source relation retains its catalogue evidence. This is daily
source-declared attribution, not proof of the identity of every inference.

## Manual pilot

Run a pilot on the same completed UTC dates for each source:

```powershell
python -m gateway fetch --source vercel --start 2026-10-16 --end 2026-11-14
python -m gateway fetch --source openrouter --start 2026-10-16 --end 2026-11-14
python -m gateway build
```

Inspect a bounded set of model endpoints and the providers catalogue:

```powershell
python -m gateway fetch --source openrouter-endpoints --model reflection/beam-1.0-501b-a23b
python -m gateway fetch --source openrouter-providers
```

Repeat `--model` for at most 50 distinct explicit author/slug IDs. The responses
are preserved as catalogue evidence; they are excluded from usage normalization.
Listing is positive catalogue evidence, not a successful inference or a delisting
policy. No automatic whole-catalogue endpoint crawl runs.

OpenRouter requires `OPENROUTER_API_KEY` in the process environment. Headers and
credentials are never serialized. Requests use the existing identifying client,
30-second timeout and three bounded attempts. A missing key is an explicit error.

Responses are byte-exact JSON under
`data/external/<source>/<start>_<end>/<sha256>[_occurrence]/response.json` with separate
request metadata. Consecutive equal responses reuse a revision; changed bytes
and a return to a previously seen state create new immutable occurrences.
An interrupted directory without a manifest is refused with its path so it can
be inspected and quarantined before retry. Build verifies every archived hash. Original snapshots
are separate from these retrospective and revisable exports.
For overlapping exports, the most recently fetched whole source/day/metric
revision wins, including its absence of formerly ranked model rows. All prior
payload revisions remain available for historical reconstruction.

Build publishes `data/analytics/gateway_usage.parquet` and
`gateway_quality.json`. It checks duplicate keys, finite nonnegative values,
requested dates, text scope, and Vercel daily share sums (0.05 percentage point
tolerance). Each returned day/metric slice must include Other; otherwise build
rejects the incomplete denominator. Missing day/metric pairs appear in the report, without zero filling.
`Other` remains an aggregate without a model ID. Vercel values are percentages;
OpenRouter values are tokens, not users, requests or revenue. Both describe
traffic on their own gateway, and their denominators must remain separate.

Optional `--ledger path.json` supplies an explicitly reviewed array of links:

```json
[{"source":"vercel","source_name":"Beam 1.0",
  "hf_repo_id":"reflection/Beam-1.0-501B-A23B",
  "valid_from":"2026-10-16","valid_until":"2026-11-14",
  "reviewer":"name","evidence_url":"https://vercel.com/ai-gateway/models/labs/reflection"}]
```

Dates are inclusive. Conflicting targets remain ambiguous. Without evidence and
a reviewer links are not accepted. No fuzzy matches or family merges run.
For OpenRouter ledger entries use `source_model_id` (the immutable permaslug)
instead of `source_name`; a display name cannot establish that identity.
Coverage reports both linked rows and linked observed value within each source,
date and metric. Attribution means identity coverage, not open-weight market
share: a linked repository does not establish the model's license.

Data attribution: [OpenRouter Rankings](https://openrouter.ai/rankings), as of
the export metadata, and [Vercel AI Gateway](https://vercel.com/ai-gateway/leaderboards).
Both export sources specify CC BY 4.0. Revisions retain `as_of` when supplied
and local `fetched_at` in all cases.
