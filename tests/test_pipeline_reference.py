"""Tests for pipeline reference module."""

from __future__ import annotations

from pathlib import Path

import pytest

from videotuner.encoding_utils import SampledSource, UsableRange
from videotuner.pipeline_cli import PipelineArgs
from videotuner.pipeline_reference import (
    MetricSamplingParams,
    are_sampling_params_equal,
)


class TestAreSamplingParamsEqual:
    """Tests for are_sampling_params_equal function."""

    def test_returns_true_when_both_enabled_and_params_match(self):
        """Test returns True when both metrics enabled with matching params."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=True,
            vmaf_interval_frames=1600,
            vmaf_region_frames=20,
            ssim2_interval_frames=1600,
            ssim2_region_frames=20,
        )
        assert are_sampling_params_equal(args) is True

    def test_returns_true_with_default_params(self):
        """Test returns True with default sampling parameters (1600/20)."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=True,
            # Using defaults: interval=1600, region=20 for both
        )
        assert are_sampling_params_equal(args) is True

    def test_returns_false_when_vmaf_disabled(self):
        """Test returns False when VMAF is disabled."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=False,
            ssim2=True,
            vmaf_interval_frames=1600,
            vmaf_region_frames=20,
            ssim2_interval_frames=1600,
            ssim2_region_frames=20,
        )
        assert are_sampling_params_equal(args) is False

    def test_returns_false_when_ssim2_disabled(self):
        """Test returns False when SSIM2 is disabled."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=False,
            vmaf_interval_frames=1600,
            vmaf_region_frames=20,
            ssim2_interval_frames=1600,
            ssim2_region_frames=20,
        )
        assert are_sampling_params_equal(args) is False

    def test_returns_false_when_both_disabled(self):
        """Test returns False when both metrics are disabled."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=False,
            ssim2=False,
        )
        assert are_sampling_params_equal(args) is False

    def test_returns_false_when_interval_frames_differ(self):
        """Test returns False when interval_frames differ between metrics."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=True,
            vmaf_interval_frames=1600,
            vmaf_region_frames=20,
            ssim2_interval_frames=800,  # Different
            ssim2_region_frames=20,
        )
        assert are_sampling_params_equal(args) is False

    def test_returns_false_when_region_frames_differ(self):
        """Test returns False when region_frames differ between metrics."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=True,
            vmaf_interval_frames=1600,
            vmaf_region_frames=20,
            ssim2_interval_frames=1600,
            ssim2_region_frames=40,  # Different
        )
        assert are_sampling_params_equal(args) is False

    def test_returns_false_when_both_params_differ(self):
        """Test returns False when both interval and region differ."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=True,
            vmaf_interval_frames=1600,
            vmaf_region_frames=20,
            ssim2_interval_frames=3200,  # Different
            ssim2_region_frames=40,  # Different
        )
        assert are_sampling_params_equal(args) is False

    def test_returns_true_with_non_default_matching_params(self):
        """Test returns True with custom matching params (not defaults)."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf=True,
            ssim2=True,
            vmaf_interval_frames=3200,
            vmaf_region_frames=50,
            ssim2_interval_frames=3200,
            ssim2_region_frames=50,
        )
        assert are_sampling_params_equal(args) is True


class TestMetricSamplingParams:
    """How a metric samples the source a job reads, across one file or several."""

    @staticmethod
    def _source(name: str, start: int, end: int) -> SampledSource:
        return SampledSource(
            path=Path(f"{name}.mkv"),
            cache_file=Path(f"{name}.ffindex"),
            usable_range=UsableRange(start=start, end=end, frame_count=end - start),
        )

    def test_is_frozen(self) -> None:
        params = MetricSamplingParams(
            interval_frames=1600,
            region_frames=20,
            sources=(self._source("one", 0, 10000),),
        )
        with pytest.raises(AttributeError):
            setattr(params, "interval_frames", 1)

    def test_num_samples_for_one_file(self) -> None:
        params = MetricSamplingParams(
            interval_frames=100,
            region_frames=50,
            sources=(self._source("one", 0, 1000),),
        )
        # (1000 + 100 - 50) // 100 = 10
        assert params.num_samples == 10
        assert params.total_sample_frames == 500

    def test_samples_are_counted_per_file_then_summed(self) -> None:
        """Each file is sampled on its own, so short files still contribute."""
        params = MetricSamplingParams(
            interval_frames=100,
            region_frames=50,
            sources=(
                self._source("one", 0, 1000),
                self._source("two", 0, 1000),
            ),
        )

        assert params.num_samples == 20
        assert params.total_sample_frames == 1000

    def test_a_file_shorter_than_the_interval_still_contributes(self) -> None:
        params = MetricSamplingParams(
            interval_frames=1600,
            region_frames=20,
            sources=(
                self._source("long", 0, 10000),
                self._source("short", 0, 500),
            ),
        )

        assert (
            params.num_samples
            > MetricSamplingParams(
                interval_frames=1600,
                region_frames=20,
                sources=(self._source("long", 0, 10000),),
            ).num_samples
        )
