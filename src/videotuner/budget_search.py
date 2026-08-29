"""Choosing, and searching for, the best encode within a bitrate budget.

When the predicted bitrate of a job's result exceeds the budget set by
``--predicted-bitrate-warning-percent``, the encodes that were actually run
still hold cheaper options. This module picks the best of them, and can run
further encodes to close in on the budget.
"""

from __future__ import annotations

from math import log
from typing import TYPE_CHECKING

from .constants import BUDGET_SEARCH_BLIND_STEP, BUDGET_SEARCH_MAX_ITERATIONS
from .crf_search import CRF_CEILING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .crf_search import QualityTarget
    from .pipeline_types import BudgetPoint


def run_budget_search(
    points: list[BudgetPoint],
    cap_kbps: float,
    interval: float,
    encode: Callable[[float], BudgetPoint],
) -> list[BudgetPoint]:
    """Encode further CRF values for one profile until the boundary is resolved.

    Each iteration is a real encode, so the cap matters: the search stops when
    the bracket closes to the CRF interval, when the encoder's ceiling is
    reached, or when it has spent its allowance, whichever comes first.

    Args:
        points: Encodes already measured for this profile
        cap_kbps: Highest predicted bitrate that fits the budget
        interval: CRF granularity, from --crf-interval
        encode: Runs one encode at a CRF and returns what it measured

    Returns:
        The points this search added, which may be empty
    """
    known = list(points)
    added: list[BudgetPoint] = []

    for _ in range(BUDGET_SEARCH_MAX_ITERATIONS):
        crf = next_budget_crf(known, cap_kbps, interval)
        if crf is None:
            break
        point = encode(crf)
        known.append(point)
        added.append(point)

    return added


def next_budget_crf(
    points: list[BudgetPoint],
    cap_kbps: float,
    interval: float,
) -> float | None:
    """Pick the next CRF worth encoding to close in on the budget.

    Seeks the lowest CRF whose predicted bitrate still fits, since lower CRF
    means higher quality. Bitrate falls roughly geometrically as CRF rises, so
    the bracketing points are interpolated on the logarithm of bitrate, which
    lands far closer than bisection and saves whole encodes.

    The search only ever looks upward, never below the cheapest CRF already
    measured, so a profile whose every encode already fits the budget is left
    alone. See the note at the ``over`` filter for when that loses anything.

    Args:
        points: Encodes already measured for one profile
        cap_kbps: Highest predicted bitrate that fits the budget
        interval: CRF granularity, from --crf-interval

    Returns:
        The CRF to encode next, or None when the boundary is already resolved
    """
    # Paired with their rate factor, so the CRF is a plain float from here on
    rated = [
        (p.crf, p) for p in points if p.crf is not None and p.predicted_bitrate_kbps > 0
    ]

    over = [item for item in rated if item[1].predicted_bitrate_kbps > cap_kbps]
    under = [item for item in rated if item[1].predicted_bitrate_kbps <= cap_kbps]
    if not over:
        # Everything measured already fits, so there is no boundary above to
        # close in on. A lower CRF might fit too and score better, but this
        # deliberately does not go looking for it.
        #
        # Reaching here at all is narrow. Every profile is searched, not just
        # the one that raised the warning, but a profile that converged is
        # ranked in the tier the winner came from and sorted by bitrate ahead
        # of it, so its optimum is over the budget whenever the winner's is.
        # A profile that stopped at the CRF floor has measured down to CRF 1.0,
        # where its bitrate is highest, so if that fits it is already both the
        # cheapest and the best point there is.
        #
        # What is left is a profile that failed to converge without reaching
        # the floor, by exhausting its iterations or being sent back to a CRF
        # already tested. That one has unexplored ground below, and is not
        # explored: it missed the targets everywhere it looked, so a lower CRF
        # would most likely miss them too, and finding out costs real encodes.
        return None

    if not under:
        return _extrapolate_upward(over, cap_kbps, interval)

    crf_low, low = max(over, key=lambda item: item[0])
    crf_high, high = min(under, key=lambda item: item[0])
    if crf_high - crf_low <= interval:
        return None  # Boundary resolved to the interval

    span = log(high.predicted_bitrate_kbps) - log(low.predicted_bitrate_kbps)
    fraction = (log(cap_kbps) - log(low.predicted_bitrate_kbps)) / span
    candidate = _round_to_interval(crf_low + fraction * (crf_high - crf_low), interval)

    # Interpolation can land on a bracket end, which would spend an encode
    # re-measuring a CRF already known. Bisection is the fallback that cannot.
    if not crf_low < candidate < crf_high:
        candidate = _round_to_interval((crf_low + crf_high) / 2.0, interval)
    if not crf_low < candidate < crf_high:
        return None

    return candidate


def _extrapolate_upward(
    over: list[tuple[float, BudgetPoint]],
    cap_kbps: float,
    interval: float,
) -> float | None:
    """Estimate where bitrate crosses the budget, above every CRF tested so far.

    Two points at different CRFs give the slope of ln(bitrate) against CRF.
    Without that, or when the slope says nothing useful, it steps blindly.

    Whatever the estimate, the result must be a CRF above every one already
    measured. A point only just over the budget estimates a step of almost
    nothing, which would round back onto itself: that spends an encode learning
    nothing, and leaves two points at one CRF and so no slope to divide by.
    """
    ranked = sorted(over, key=lambda item: item[0])
    crf_highest, highest = ranked[-1]
    if crf_highest >= CRF_CEILING:
        return None  # Nowhere left to go

    step = BUDGET_SEARCH_BLIND_STEP
    if len(ranked) >= 2:
        crf_previous, previous = ranked[-2]
        if crf_previous != crf_highest:
            slope = (
                log(highest.predicted_bitrate_kbps)
                - log(previous.predicted_bitrate_kbps)
            ) / (crf_highest - crf_previous)
            if slope < 0:
                step = (log(cap_kbps) - log(highest.predicted_bitrate_kbps)) / slope

    candidate = _round_to_interval(crf_highest + step, interval)
    if candidate <= crf_highest:
        # Estimate rounded away to nothing, so advance by the smallest step
        # the search can actually resolve.
        candidate = _round_to_interval(crf_highest + interval, interval)

    candidate = min(candidate, CRF_CEILING)
    return candidate if candidate > crf_highest else None


def _round_to_interval(crf: float, interval: float) -> float:
    """Snap a CRF to the search granularity."""
    return round(round(crf / interval) * interval, 4)


def select_within_budget(
    points: list[BudgetPoint],
    cap_kbps: float,
    targets: list[QualityTarget],
) -> BudgetPoint | None:
    """Pick the best measured encode whose predicted bitrate fits the budget.

    Args:
        points: Every encode that was run and scored, across all profiles
        cap_kbps: Highest predicted bitrate a point may have to qualify
        targets: Quality targets, used to promote the metrics being aimed at

    Returns:
        The chosen point, or None if nothing fits within the budget
    """
    from .pipeline_multi_profile import (
        get_effective_metric_priority,
        metric_priority_sort_key,
    )
    from .pipeline_validation import check_scores_meet_targets

    candidates = [p for p in points if 0 < p.predicted_bitrate_kbps <= cap_kbps]
    if not candidates:
        return None

    priority = get_effective_metric_priority(targets)
    candidates.sort(
        key=lambda p: (
            0 if check_scores_meet_targets(p.scores, targets) else 1,
            metric_priority_sort_key(p.scores, priority),
        )
    )
    return candidates[0]
