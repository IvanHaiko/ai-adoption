# 1. Snapshots live in git until the capstone starts, then move to S3

- **Status:** accepted
- **Date:** 2026-08-29
- **Supersedes:** nothing
- **Revisit on:** ~2026-12-19, after the capstone (moved from ~2026-11-21; see the 2026-11-13 revision)

## Context

The usage series cannot be backfilled. HuggingFace `downloads` is a rolling
30-day figure with no history in the API, and OpenRouter publishes no usage
history at all. Collection therefore had to start before the warehouse, the
schema, or the object store existed.

The collector runs from GitHub Actions only — there is no always-on local
machine — so a runner with no persistent disk has to put each snapshot
somewhere durable in the same job that produced it.

Measured on 2026-08-29 by running the collector:

| | |
|---|---|
| One day, gzipped | 532 KB (4.75 MB raw, ~9x) |
| ~84 days until the capstone starts | ~45 MB |
| One year | ~194 MB |

**Revised 2026-08-29**, same day, after adding the `hf_top_models` leg: a day
is now **1 012 KB**, ~**111 MB** to the start of the capstone and ~**370 MB**
a year. The decision below is unchanged - git carries 111 MB without noticing -
but the margin is half what it was, and a second leg of this size would be
worth re-deciding rather than assuming.

## Decision

Commit snapshots to `data/raw/` in this repository. Move to S3 at the start of
the capstone, and leave the git-era snapshots in history rather than rewriting
it.

## Consequences

**Accepted cost.** Git history is append-only in practice. The ~45 MB collected
before the move stays in the repository forever; removing it would need
`git filter-repo` and a force-push that breaks every existing clone. At this
size that is not worth doing, and the decision is only cheap because the move
is scheduled early — the same choice would be wrong for a year of collection.

**Benefit taken.** A `git clone` is a complete second copy, with history, at no
cost and with no credentials. That covers the offsite-backup requirement
without a third service, and `scripts/pull_snapshots.ps1` turns it into a daily
staleness check as well.

**The port is small, and was measured rather than assumed.** Filesystem access
is confined to `collector/storage.py` plus four call sites — `manifest.py:37`,
`manifest.py:39`, `manifest.py:61`, `run.py:38` and `run.py:42`. The `sources/`
modules never touch the disk; they go through `Context.store` / `Context.load`.
Moving to S3 means writing an `S3Storage` with `exists`, `read`, `write` and
swapping it into `Context`. The key layout is already S3-shaped:
`raw/<YYYY-MM-DD>/<file>` is a usable prefix as it stands.

No abstraction was introduced now to prepare for this. With four call sites,
the seam is as cheap to cut in November as it is today, and a storage interface
written against one implementation would be a guess about the second.

## Revision 2026-11-13: the move is deferred past the capstone

Measured 2026-11-13 on 78 days: `data/raw` takes **216 MB** of git history and
`.git` **236 MB**. A day now costs **2.8-6.1 MB** (last seven days), not
1.2 MB - the `hf_cohort` leg and large arXiv days added most of it. The largest
single file is 4.8 MB, far below GitHub's 50/100 MB per-file limits. At about
4 MB a day the four capstone weeks add roughly 115 MB, to about 350 MB.

The move to S3 is deferred until the capstone is over. Doing it in the first
week of a four-week build costs one to two days and puts the one irrecoverable
asset - the daily series - at risk during a storage migration, for a gain
(faster clones, an S3 line) that does not change what the capstone is judged
on. The decision is the original one, re-dated; the port remains as small as
measured above, and the git-era history still stays in place rather than being
rewritten. Revisit sooner if a single day passes 20 MB or the repository passes
1 GB.

**Git LFS** was considered and rejected: GitHub's free quota is 1 GB of storage
and 1 GB of bandwidth a month, which this growth rate and every CI checkout
would exhaust, and it would not remove the history already committed.

## Alternatives considered

- **A separate data repository.** Keeps this repository's history clean and can
  simply be archived after the move. Rejected: it needs a fine-grained PAT in
  secrets, and 45 MB does not justify the extra moving part.
- **S3 from the start.** Avoids doing the storage work twice. Rejected on
  timing: it costs account setup, credentials and `boto3` now, in exchange for
  saving roughly 60 lines in November — while every day spent setting it up is
  a day of the series that does not exist.
- **Cloudflare R2.** Cheaper and without egress fees. Rejected deliberately:
  `S3` is the line a hiring manager recognises, and at 194 MB/year the price
  difference is not real money.
- **GitHub Actions artifacts.** Retention is bounded and configurable; this is
  log storage, not a system of record.
