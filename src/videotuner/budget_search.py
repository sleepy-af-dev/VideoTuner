"""Choosing, and searching for, the best encode within a bitrate budget.

When the predicted bitrate of a job's result exceeds the budget set by
``--predicted-bitrate-warning-percent``, the encodes that were actually run
still hold cheaper options. This module picks the best of them, and can run
further encodes to close in on the budget.
"""

from __future__ import annotations

from math import log
from typing import TYPE_CHECKING

from .constants import BUDGET_SEARCH_MAX_ITERATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from .crf_search import QualityTarget
    from .pipeline_types import BudgetPoint


def run_budget_search(
    points: list[BudgetPoint],
    cap_kbps: float,
    interval: float,
    encode: Callable[[float], BudgetPoint],
    max_iterations: int = BUDGET_SEARCH_MAX_ITERATIONS,
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
        max_iterations: Most extra encodes this profile may spend

    Returns:
        The points this search added, which may be empty
    """
    known = list(points)
    added: list[BudgetPoint] = []

    for _ in range(max_iterations):
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

    Args:
        points: Encodes already measured for one profile
        cap_kbps: Highest predicted bitrate that fits the budget
        interval: CRF granularity, from --crf-interval

    Returns:
        The CRF to encode next, or None when the boundary is already resolved
    """
    rated = [p for p in points if p.crf is not None and p.predicted_bitrate_kbps > 0]

    over = [p for p in rated if p.predicted_bitrate_kbps > cap_kbps]
    under = [p for p in rated if p.predicted_bitrate_kbps <= cap_kbps]
    if not over:
        # Only reached with a point above the budget, which is what raised the
        # warning in the first place, so there is no boundary to find here.
        return None

    low = max(over, key=lambda p: p.crf or 0.0)
    if not under:
        return _extrapolate_upward(over, cap_kbps, interval)

    high = min(under, key=lambda p: p.crf or 0.0)

    crf_low = low.crf or 0.0
    crf_high = high.crf or 0.0
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
    over: list[BudgetPoint],
    cap_kbps: float,
    interval: float,
) -> float | None:
    """Estimate where bitrate crosses the budget, above every CRF tested so far.

    Two points give the slope of ln(bitrate) against CRF; one gives nothing to
    extrapolate from, so it steps by a fixed amount instead.
    """
    from .crf_search import CRF_CEILING

    ranked = sorted(over, key=lambda p: p.crf or 0.0)
    highest = ranked[-1]
    crf_highest = highest.crf or 0.0
    if crf_highest >= CRF_CEILING:
        return None  # Nowhere left to go

    if len(ranked) < 2:
        return min(_round_to_interval(crf_highest + 2.0, interval), CRF_CEILING)

    previous = ranked[-2]
    crf_previous = previous.crf or 0.0
    slope = (
        log(highest.predicted_bitrate_kbps) - log(previous.predicted_bitrate_kbps)
    ) / (crf_highest - crf_previous)
    if slope >= 0:
        # Bitrate did not fall with CRF, so the model says nothing useful
        return min(_round_to_interval(crf_highest + 2.0, interval), CRF_CEILING)

    needed = (log(cap_kbps) - log(highest.predicted_bitrate_kbps)) / slope
    return min(_round_to_interval(crf_highest + needed, interval), CRF_CEILING)


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
