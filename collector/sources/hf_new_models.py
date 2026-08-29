"""New arrivals: text-generation repositories ordered by creation date.

This leg exists because ranking by downloads is the wrong axis for the question
the project asks. A model that was released this week has, by definition, not
accumulated downloads yet, so a downloads threshold excludes exactly the
population whose adoption curve is the thesis. Measured 2026-08-29 on 34 000
paged repositories:

    repositories created in the last 30 days          8 044
      of them inside the top  1 000 by downloads         64   (0.8%)
      of them inside the top  5 000 by downloads        537   (6.7%)

    median age of a repository ranked      1-1 000      487 days
    median age of a repository ranked 10 000-20 000     142 days

The head of the ranking is a year and a half old. Watching only it is
survivorship bias against every model this project is about.

TARGET_ROWS is sized from arrival rate, not from a round number: roughly
460-500 text-generation repositories are created a day, so 2 000 newest covers
about four days of arrivals. That redundancy is deliberate - a missed
collection day is unrecoverable, and a four-fold overlap means one has to miss
four consecutive days before a new repository is never seen at all.

Note what this leg does and does not rescue. A repository's *existence* is
recoverable later: `createdAt` and `base_model` can be read retrospectively at
any time, so the derivative graph can always be rebuilt. Its early `downloads`
cannot. That early curve is the measurement, which is why this leg is as
time-critical as the ranking one.
"""
from __future__ import annotations

from .hf_list import collect_paged

LEG = "hf_new_models"
SORT = "createdAt"
TARGET_ROWS = 2000


def collect(ctx, previous: dict) -> dict:
    return collect_paged(ctx, LEG, SORT, TARGET_ROWS)
