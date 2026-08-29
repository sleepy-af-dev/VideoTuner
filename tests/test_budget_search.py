"""Tests for budget search: choosing and finding encodes within a bitrate budget."""

from videotuner.budget_search import select_within_budget
from videotuner.crf_search import QualityTarget
from videotuner.pipeline_types import BudgetPoint


def _point(
    name: str,
    bitrate_kbps: float,
    scores: dict[str, float | None],
    *,
    crf: float | None = None,
) -> BudgetPoint:
    """Build a measured point, which the budget tests vary in few fields."""
    return BudgetPoint(
        profile_name=name,
        crf=crf,
        scores=scores,
        predicted_bitrate_kbps=bitrate_kbps,
    )


class TestSelectWithinBudget:
    """Tests for select_within_budget function."""

    def test_picks_point_at_or_below_cap(self):
        """A point within the cap is chosen over one that exceeds it."""
        over = _point("alpha", 78000.0, {"vmaf_mean": 96.0}, crf=18.0)
        under = _point("alpha", 68000.0, {"vmaf_mean": 94.0}, crf=22.0)

        chosen = select_within_budget([over, under], 70000.0, [])

        assert chosen is not None
        assert chosen.crf == 22.0

    def test_prefers_target_passer_over_higher_scoring_failer(self):
        """Within the budget, meeting every target beats a higher leading score."""
        # Higher vmaf_mean, the leading metric, but misses the ssim2_mean target
        fails = _point(
            "alpha", 60000.0, {"vmaf_mean": 96.0, "ssim2_mean": 80.0}, crf=24.0
        )
        meets = _point(
            "bravo", 68000.0, {"vmaf_mean": 91.0, "ssim2_mean": 86.0}, crf=22.0
        )
        targets = [QualityTarget("vmaf_mean", 90.0), QualityTarget("ssim2_mean", 85.0)]

        chosen = select_within_budget([fails, meets], 70000.0, targets)

        assert chosen is not None
        assert chosen.profile_name == "bravo"

    def test_ranks_by_promoted_metric(self):
        """Targeting a metric promotes it above the default leading metric."""
        higher_vmaf = _point(
            "alpha", 60000.0, {"vmaf_mean": 96.0, "ssim2_5pct": 70.0}, crf=24.0
        )
        higher_ssim2 = _point(
            "bravo", 68000.0, {"vmaf_mean": 94.0, "ssim2_5pct": 80.0}, crf=22.0
        )
        points = [higher_ssim2, higher_vmaf]

        # No targets: vmaf_mean leads the default priority
        assert select_within_budget(points, 70000.0, []) is higher_vmaf

        # ssim2_5pct targeted: promoted above vmaf_mean, flipping the choice.
        # Both miss it, so the tier does not decide this.
        targets = [QualityTarget("ssim2_5pct", 90.0)]
        assert select_within_budget(points, 70000.0, targets) is higher_ssim2

    def test_picks_across_profiles_not_just_within_one(self):
        """The best point may come from a profile other than the one that won."""
        alpha = _point("alpha", 68000.0, {"vmaf_mean": 92.0}, crf=22.0)
        bravo = _point("bravo", 67000.0, {"vmaf_mean": 94.0}, crf=23.0)

        chosen = select_within_budget([alpha, bravo], 70000.0, [])

        assert chosen is not None
        assert chosen.profile_name == "bravo"

    def test_excludes_points_without_a_usable_bitrate(self):
        """A point with no measured bitrate does not count as fitting the budget."""
        unusable = _point("alpha", 0.0, {"vmaf_mean": 99.0}, crf=20.0)
        usable = _point("bravo", 68000.0, {"vmaf_mean": 91.0}, crf=22.0)

        chosen = select_within_budget([unusable, usable], 70000.0, [])

        assert chosen is not None
        assert chosen.profile_name == "bravo"

    def test_returns_none_when_nothing_fits(self):
        """Every point exceeding the budget means there is nothing to offer."""
        over = _point("alpha", 78000.0, {"vmaf_mean": 96.0}, crf=18.0)

        assert select_within_budget([over], 70000.0, []) is None
