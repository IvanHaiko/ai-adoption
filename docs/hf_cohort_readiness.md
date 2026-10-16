# Analytical readiness and deployment proposal

This is the retained **v1 assessment**. The corrected deployment recommendation is
`hf_lifecycle_20261015_v2`; see the [architecture freeze and activation checklist](hf_cohort_architecture_freeze.md).
The historical v1 population, overlap and storage figures below remain v1 evidence.

A fixed cohort of **1,500 repositories** was enrolled locally as
`hf_lifecycle_20261014_v1`, selection version 1, on
2026-10-14 at 14:05:56 UTC (16:05:56 Europe/Berlin).
The cutoff is 2026-10-13 23:59:59 UTC. No later archive or live API
observations participated. The registry embeds all 32,983 eligible candidate
records and input hashes so membership can be independently reproduced.
The preliminary archive inventory found 32,986 IDs; the selection reader uses
completed discovery legs and validated exact-identity envelopes.

| Selection group | Members | Organizations | Median prior observed days | In latest discovery snapshot |
| --- | ---: | ---: | ---: | ---: |
| Popular | 500 | 145 | 46 | 499 |
| Recent | 500 | 237 | 1 | 499 |
| Remaining | 500 | 500 | 4 | 82 |
| Total | 1,500 | Groups overlap | 4 | 1,080 |

All 1,500 members already have at least one historical observation; 618 have
at least five. Prior density ranges from one to 47 days. The remaining group
includes many repositories absent from current rankings, addressing the
continuity gap directly. There are no duplicate cohort IDs or repository IDs.
Repeated initialization preserves the registry bytes, and replay from the
embedded eligible candidates reproduces all members and selection groups.

With uninterrupted successful daily collection after activation, each member
can gain up to **60 new observations over 60 days**: 90,000 repository/day
observations altogether. At 95% measurement coverage the scenario is 85,500
observations (57 per member on average, not a guarantee for each repository).
Future observations do not exist yet. Missing days and unavailable repositories
remain explicit missingness; they cannot be reconstructed from later HF counts.

This supports trajectories after leaving rankings, rolling download growth and
decline, persistence of engagement, comparisons between initially popular and
recent repositories, and analysis of attrition with an explicit denominator.
HF downloads are a **rolling 30-day measure**, not daily downloads, lifetime
usage, unique users or production deployments. Likes are engagement proxies.

Selection biases remain: the starting population is discovered by the existing
text-generation lists and OpenRouter links, popularity and recency quotas are
purposive, measurements used for selection have different ages, missing metadata
and unavailable identities affect eligibility, and organizations are namespaces
rather than independent model families. The remaining group iterates organization
names lexically and hashes IDs within organizations: early lexical organizations
can be favored when the quota is smaller than the number of groups. No ecosystem
representativeness, uniform inclusion probability or causal adoption claim is made.
Fixed membership preserves disappeared members; renames are diagnosed rather
than automatically remapped or replaced.

## Requests, duration and storage

The 2026-10-13 local discovery snapshot overlaps **1,080** members. If that
pattern persists, approximately **420 extra primary requests/day** are needed;
this is an archived overlap proxy, not measured future coverage. Their request
spacing floor is 5.25 minutes. Network latency and retries increase that time.
As discoveries change, primary requests can rise to 1,500 per run (18.75 minutes
at the spacing floor). Three attempts give a theoretical ceiling of 4,500 HTTP
attempts per run; four retry slots can theoretically reach 18,000/day before the
polling time budget limits work. Complete-day reruns make zero requests.

Cohort requests run sequentially, with a 0.75-second floor and existing bounded
retries. A 30-minute monotonic polling budget defers unresolved members and saves
successes for a later slot. One in-flight logical request can overrun that budget
by its bounded timeout/backoff duration (roughly three minutes with current
settings). Deferred IDs are separate from attempted failures. A UTC date boundary
also stops requests and prevents late responses becoming old-day measurements.
HF's currently documented anonymous quota is 500 API calls per five minutes;
shared-IP usage and earlier discovery calls mean throttling can still occur.

The persistent registry occupies **9.80 MB** once. Using archived model bodies
as a storage proxy, 1,500 member envelopes occupy **1.71 MB raw / 0.249 MB gzip**,
or roughly **14.95 MB gzip over 60 days**. Real detail responses may be larger;
this is not a hard bound or an actual cohort daily payload. Git history retains
committed bytes. Registry data is immutable enrollment metadata, rather than
production measurements or rewritten historical snapshots.

The existing deployment uses GitHub Actions and Git-backed snapshots. Read-only
GitHub inspection verified that `IvanHaiko/ai-adoption` is **public**, so standard
GitHub-hosted runner minutes are currently free. This design provisions no AWS
resources and its **incremental AWS cost is $0**. Live AWS accounts/bills were not
queried; the repository contains no AWS deployment configuration. No paid Actions
artifact storage is introduced. In a future private deployment beyond included
minutes, the currently documented standard Linux rate is $0.006/minute.

## Proposed deployment and status

| State | Status |
| --- | --- |
| Implemented and locally validated | See independent validation report |
| Local persistent registry created | Yes, 1,500 fixed members |
| Deployed to AWS | No; no AWS changes proposed |
| Cohort enabled in daily scheduling | No |
| Actual successful scheduled cohort run verified | No |

The existing discovery-only scheduled run completed on 2026-10-14. Its production
manifest contains the original seven legs and no cohort leg; it is not evidence
that this feature operates in production.

After human approval: commit and deploy the scoped source changes, immutable
registry, tests and documentation to `main`, then set repository variable
`HF_COHORT_ID=hf_lifecycle_20261015_v2` (the corrected cohort). No new schedule or cloud services are
needed. A day already marked complete remains unchanged; verification must use
the first newly collected day after activation. Check its `hf_cohort` coverage,
failures/deferred diagnostics, gzip hash, and a rerun with zero new HTTP calls.
For analysis, build Silver/Gold into a fresh output directory and validate it
before promoting outputs; this task does not overwrite existing tables.

To disable future tracking, clear `HF_COHORT_ID`. Preserve the registry and all
observations. Do not replace missing members or rewrite prior days. Approval is
required before deployment or live configuration changes by the task's explicit
production boundary; no deployment has been performed.

Sources checked 2026-10-14:

- [HF rate limits](https://huggingface.co/docs/hub/en/rate-limits)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Existing scheduled discovery run](https://github.com/IvanHaiko/ai-adoption/actions/runs/38560358350)
- [Technical instructions](hf_cohort_tracking.md)
