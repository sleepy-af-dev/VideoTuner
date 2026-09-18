"""Tests for pipeline_reference, and for the reference no longer being a file."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from videotuner import pipeline_reference
from videotuner.pipeline_cli import PipelineArgs
from videotuner.pipeline_reference import are_sampling_params_equal
from videotuner.pipeline_types import IterationContext


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


def test_no_lossless_reference_is_generated() -> None:
    # Both metrics select their reference frames from the source with the
    # same script the encoder is fed, proven frame-identical in
    # tests/test_frame_accuracy.py, so encoding a reference copy is work
    # with no product.
    assert not hasattr(pipeline_reference, "generate_metric_reference")


def test_no_iteration_can_be_pointed_at_a_reference_file() -> None:
    # The stronger half of the claim above: with the fields gone there is
    # nowhere for a reference path to be threaded through, so a metric
    # cannot quietly go back to scoring against a copy.
    fields = {f.name for f in dataclasses.fields(IterationContext)}
    assert "vmaf_ref_path" not in fields
    assert "ssim2_ref_path" not in fields
