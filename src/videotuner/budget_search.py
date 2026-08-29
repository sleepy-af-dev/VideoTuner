"""Choosing, and searching for, the best encode within a bitrate budget.

When the predicted bitrate of a job's result exceeds the budget set by
``--predicted-bitrate-warning-percent``, the encodes that were actually run
still hold cheaper options. This module picks the best of them, and can run
further encodes to close in on the budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .crf_search import QualityTarget
    from .pipeline_types import BudgetPoint


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
