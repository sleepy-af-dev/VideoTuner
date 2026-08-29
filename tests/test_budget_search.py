"""Tests for budget search: choosing and finding encodes within a bitrate budget."""

import math

from videotuner.budget_search import (
    next_budget_crf,
    run_budget_search,
    select_within_budget,
)
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


class TestNextBudgetCrf:
    """Choosing the next CRF to encode when closing in on the budget."""

    def test_interpolates_between_the_bracketing_points(self):
        """Bitrate falls geometrically with CRF, so interpolate on its logarithm.

        Bracketed by CRF 18 at 78,000 kbps and CRF 26 at 30,000 kbps, against a
        43,000 kbps budget. Working the log-linear formula by hand:
        t = ln(43000/78000) / ln(30000/78000) = 0.623, so 18 + 0.623 * 8 = 22.98,
        which rounds to 23.0 on a 0.5 interval. Bisection would have said 22.0.
        """
        points = [
            _point("alpha", 78000.0, {"vmaf_mean": 99.0}, crf=18.0),
            _point("alpha", 30000.0, {"vmaf_mean": 95.0}, crf=26.0),
        ]

        assert next_budget_crf(points, 43000.0, 0.5) == 23.0

    def test_extrapolates_upward_when_nothing_fits_yet(self):
        """With every encode over budget there is no upper bracket to bisect.

        From CRF 18 at 78,000 kbps and CRF 22 at 60,000 kbps, the slope of
        ln(bitrate) is (ln 60000 - ln 78000) / 4 = -0.0656 per CRF. Reaching
        43,000 kbps from CRF 22 needs (ln 43000 - ln 60000) / -0.0656 = 5.08
        more, giving 27.08, which rounds to 27.0.
        """
        points = [
            _point("alpha", 78000.0, {"vmaf_mean": 99.0}, crf=18.0),
            _point("alpha", 60000.0, {"vmaf_mean": 97.0}, crf=22.0),
        ]

        assert next_budget_crf(points, 43000.0, 0.5) == 27.0

    def test_never_proposes_a_crf_already_encoded(self):
        """A point just over the budget pulls the interpolation onto itself.

        CRF 23 sits at 43,500 kbps against a 43,000 kbps budget, so
        interpolating between it and CRF 26 lands back on 23.0. Re-encoding a
        CRF already measured would spend an encode to learn nothing, so it
        falls back to the midpoint of the bracket.
        """
        points = [
            _point("alpha", 78000.0, {"vmaf_mean": 99.0}, crf=18.0),
            _point("alpha", 43500.0, {"vmaf_mean": 96.0}, crf=23.0),
            _point("alpha", 30000.0, {"vmaf_mean": 95.0}, crf=26.0),
        ]

        assert next_budget_crf(points, 43000.0, 0.5) == 24.5

    def test_stops_once_the_bracket_is_within_the_interval(self):
        """Nothing encodable lies between adjacent CRFs, so the boundary is found."""
        points = [
            _point("alpha", 43500.0, {"vmaf_mean": 96.0}, crf=21.5),
            _point("alpha", 42800.0, {"vmaf_mean": 95.8}, crf=22.0),
        ]

        assert next_budget_crf(points, 43000.0, 0.5) is None

    def test_stops_at_the_crf_ceiling(self):
        """Even the cheapest rate factor the encoder offers is over budget."""
        points = [
            _point("alpha", 90000.0, {"vmaf_mean": 99.0}, crf=48.0),
            _point("alpha", 80000.0, {"vmaf_mean": 98.0}, crf=51.0),
        ]

        assert next_budget_crf(points, 43000.0, 0.5) is None

    def test_ignores_bitrate_mode_encodes(self):
        """A bitrate-mode encode has no rate factor, so it cannot bracket a CRF."""
        points = [_point("alpha", 78000.0, {"vmaf_mean": 99.0}, crf=None)]

        assert next_budget_crf(points, 43000.0, 0.5) is None


class TestRunBudgetSearch:
    """The loop that spends encodes closing in on the budget."""

    def test_closes_the_boundary_without_running_forever(self):
        """A stub encoder stands in for real encoding, proving the loop ends.

        Bitrate is modelled as falling geometrically with CRF, crossing the
        43,000 kbps budget at CRF 21.95. The search should bracket that to
        within the 0.5 interval and stop, well inside its iteration cap.
        """
        encoded: list[float] = []

        def encode(crf: float) -> BudgetPoint:
            encoded.append(crf)
            return _point(
                "alpha",
                200000.0 * math.exp(-0.07 * crf),
                {"vmaf_mean": 99.0 - crf * 0.1},
                crf=crf,
            )

        start = [encode(18.0)]

        found = run_budget_search(start, 43000.0, 0.5, encode)

        assert found, "the search should have run at least one encode"
        assert len(found) <= 6, "the iteration cap should bound the encodes"
        assert any(p.predicted_bitrate_kbps <= 43000.0 for p in found)
        # Resolved: nothing further is worth encoding
        assert next_budget_crf(start + found, 43000.0, 0.5) is None

    def test_runs_nothing_when_the_boundary_is_already_known(self):
        """No encode is spent when the existing points already bracket it."""

        def encode(crf: float) -> BudgetPoint:
            raise AssertionError(f"should not have encoded CRF {crf}")

        points = [
            _point("alpha", 43500.0, {"vmaf_mean": 96.0}, crf=21.5),
            _point("alpha", 42800.0, {"vmaf_mean": 95.8}, crf=22.0),
        ]

        assert run_budget_search(points, 43000.0, 0.5, encode) == []
