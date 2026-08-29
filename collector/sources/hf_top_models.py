"""The stable core: text-generation repositories ranked by downloads.

The catalogue leg (`hf_models`) only asks about repositories OpenRouter names,
which was 152 on the day this was written - enough to describe what OpenRouter
serves, nowhere near enough to measure adoption. OpenRouter's own rankings page
is thinner still: its embedded payload held 20 records across two dates,
about ten models a day.

TOP_N is set from the download distribution, measured 2026-08-29 by paging
34 000 repositories, and from nothing else:

     top    100   60.3% of all downloads in the category
     top  1 000   92.4%
     top  5 000   97.9%
     top 10 000   98.9%   (the 10 000th repo: 635 downloads in 30 days)

Three things to know before computing anything from this leg.

`downloads` is a rolling 30-day figure, not a daily increment. Differencing it
gives a noisy pseudo-daily series; the shape of a release curve survives that,
a precise daily count does not.

Rank counts *repositories*, not models. Much of the tail is quantisations and
re-uploads - `unsloth/...`, `...-GGUF`, `mradermacher/...` - so a model's real
adoption is spread across derivatives. 64% of the top 5 000 declare a
`base_model:` tag, which is what makes rolling them back up possible. Note that
coverage in downloads and coverage in derivative repositories are far apart:
the top 5 000 holds 97.9% of downloads but only 13.7% of the 23 357 derivative
repositories in the paged 34 000. Say which one a published figure means.

This leg is biased towards old models by construction, which is why
`hf_new_models` exists beside it: of 8 044 repositories created in the last 30
days, only 537 - 6.7% - appear in the top 5 000.
"""
from __future__ import annotations

from .hf_list import collect_paged

LEG = "hf_top_models"
SORT = "downloads"
TOP_N = 5000


def collect(ctx, previous: dict) -> dict:
    return collect_paged(ctx, LEG, SORT, TOP_N)
