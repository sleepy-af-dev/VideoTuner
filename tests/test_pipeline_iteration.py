"""Tests for pipeline iteration module."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from videotuner import encoding_utils
from videotuner.encoding_utils import CropValues, SampledSource, UsableRange
from videotuner.media import VideoInfo
from videotuner.pipeline_cli import PipelineArgs
from videotuner.pipeline_iteration import (
    MetricSampleParams,
    build_reference_script,
    calculate_metric_params,
    extract_scores,
    reference_video_info,
)
from videotuner.pipeline_types import IterationContext


class TestMetricSampleParams:
    """Tests for MetricSampleParams dataclass."""

    def test_creation_with_all_fields(self):
        """Test that MetricSampleParams can be created with all fields."""
        params = MetricSampleParams(
            metric_type="vmaf",
            num_samples=5,
            total_frames=2500,
            interval_frames=1000,
            region_frames=500,
        )
        assert params.metric_type == "vmaf"
        assert params.num_samples == 5
        assert params.total_frames == 2500
        assert params.interval_frames == 1000
        assert params.region_frames == 500

    def test_is_frozen(self):
        """Test that MetricSampleParams is immutable."""
        params = MetricSampleParams(
            metric_type="ssim2",
            num_samples=3,
            total_frames=1500,
            interval_frames=500,
            region_frames=500,
        )
        with pytest.raises(AttributeError):
            setattr(params, "num_samples", 10)


class TestCalculateMetricParams:
    """Tests for calculate_metric_params function."""

    def test_calculates_vmaf_params(self):
        """Test that VMAF params are calculated correctly."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf_interval_frames=1000,
            vmaf_region_frames=500,
        )
        # Use cast since calculate_metric_params only needs usable_frames and args
        ctx = cast(
            IterationContext,
            cast(object, SimpleNamespace(usable_frames=9000, args=args)),
        )

        params = calculate_metric_params(ctx, "vmaf")

        assert params.metric_type == "vmaf"
        assert params.interval_frames == 1000
        assert params.region_frames == 500
        # num_samples = (9000 + 1000 - 500) // 1000 = 9500 // 1000 = 9
        assert params.num_samples == 9
        # total_frames = 9 * 500 = 4500
        assert params.total_frames == 4500

    def test_calculates_ssim2_params(self):
        """Test that SSIM2 params are calculated correctly."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            ssim2_interval_frames=2000,
            ssim2_region_frames=300,
        )
        ctx = cast(
            IterationContext,
            cast(object, SimpleNamespace(usable_frames=9000, args=args)),
        )

        params = calculate_metric_params(ctx, "ssim2")

        assert params.metric_type == "ssim2"
        assert params.interval_frames == 2000
        assert params.region_frames == 300
        # num_samples = (9000 + 2000 - 300) // 2000 = 10700 // 2000 = 5
        assert params.num_samples == 5
        # total_frames = 5 * 300 = 1500
        assert params.total_frames == 1500

    def test_handles_small_usable_frames(self):
        """Test that calculation handles edge case of small usable frames."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf_interval_frames=500,
            vmaf_region_frames=300,
        )
        ctx = cast(
            IterationContext,
            cast(object, SimpleNamespace(usable_frames=400, args=args)),
        )

        params = calculate_metric_params(ctx, "vmaf")

        # num_samples = (400 + 500 - 300) // 500 = 600 // 500 = 1
        assert params.num_samples == 1
        assert params.total_frames == 300


class TestExtractScores:
    """Tests for extract_scores function."""

    def test_extracts_vmaf_scores(self):
        """Test that VMAF scores are extracted correctly."""
        vmaf_result = MagicMock()
        vmaf_result.mean = 95.5
        vmaf_result.harmonic_mean = 94.0
        vmaf_result.p1_low = 90.0
        vmaf_result.minimum = 85.0

        scores = extract_scores([vmaf_result], [])

        assert scores["vmaf_mean"] == 95.5
        assert scores["vmaf_hmean"] == 94.0
        assert scores["vmaf_1pct"] == 90.0
        assert scores["vmaf_min"] == 85.0

    def test_extracts_ssim2_scores(self):
        """Test that SSIM2 scores are extracted correctly."""
        ssim2_result = MagicMock()
        ssim2_result.mean = 85.0
        ssim2_result.median = 86.0
        ssim2_result.p95_high = 90.0
        ssim2_result.p5_low = 80.0

        scores = extract_scores([], [ssim2_result])

        assert scores["ssim2_mean"] == 85.0
        assert scores["ssim2_median"] == 86.0
        assert scores["ssim2_95pct"] == 90.0
        assert scores["ssim2_5pct"] == 80.0

    def test_extracts_both_metrics(self):
        """Test that both VMAF and SSIM2 scores are extracted."""
        vmaf_result = MagicMock()
        vmaf_result.mean = 95.5
        vmaf_result.harmonic_mean = 94.0
        vmaf_result.p1_low = 90.0
        vmaf_result.minimum = 85.0

        ssim2_result = MagicMock()
        ssim2_result.mean = 85.0
        ssim2_result.median = 86.0
        ssim2_result.p95_high = 90.0
        ssim2_result.p5_low = 80.0

        scores = extract_scores([vmaf_result], [ssim2_result])

        assert len(scores) == 8
        assert "vmaf_mean" in scores
        assert "ssim2_mean" in scores

    def test_handles_empty_results(self):
        """Test that empty results produce empty scores dict."""
        scores = extract_scores([], [])
        assert scores == {}

    def test_uses_first_result_only(self):
        """Test that only the first result is used when multiple provided."""
        vmaf1 = MagicMock()
        vmaf1.mean = 95.0
        vmaf1.harmonic_mean = 94.0
        vmaf1.p1_low = 90.0
        vmaf1.minimum = 85.0

        vmaf2 = MagicMock()
        vmaf2.mean = 80.0
        vmaf2.harmonic_mean = 79.0
        vmaf2.p1_low = 75.0
        vmaf2.minimum = 70.0

        scores = extract_scores([vmaf1, vmaf2], [])

        assert scores["vmaf_mean"] == 95.0  # From vmaf1, not vmaf2


_SOURCE = SampledSource(
    path=Path("source.mkv"),
    cache_file=Path("source.ffindex"),
    usable_range=UsableRange(start=0, end=240, frame_count=240),
)

_PARAMS = MetricSampleParams(
    metric_type="vmaf",
    num_samples=10,
    total_frames=20,
    interval_frames=24,
    region_frames=2,
)


def _ctx(*, crop_detect: bool, crop: CropValues | None) -> IterationContext:
    """A context carrying only what the two helpers below read."""
    return cast(
        IterationContext,
        cast(
            object,
            SimpleNamespace(
                sources=[_SOURCE],
                info=VideoInfo(fps=24.0, duration=10.0, width=3840, height=2160),
                repo_root=Path("/repo"),
                crop_values=crop,
                args=PipelineArgs(input=Path("test.mkv"), crop_detect=crop_detect),
            ),
        ),
    )


class TestReferenceComesFromTheSharedGenerator:
    """tests/test_frame_accuracy.py proves one generator selects the frames
    the encoder gets. A second implementation of that selection, however
    faithful today, would sit outside the proof, so what the metrics score
    against has to be the same function's output.
    """

    def test_the_script_is_the_sampling_script(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake(**kwargs: object) -> str:
            captured.update(kwargs)
            return "sampled = <selected>"

        monkeypatch.setattr(encoding_utils, "build_sampling_vpy_script", fake)

        got = build_reference_script(_ctx(crop_detect=False, crop=None), _PARAMS)

        assert got == "sampled = <selected>"
        assert captured["sources"] == [_SOURCE]
        assert captured["interval_frames"] == 24
        assert captured["region_frames"] == 2
        assert captured["fps"] == 24.0

    @pytest.mark.parametrize("crop_detect", [True, False])
    def test_crop_is_gated_the_way_the_encoder_gates_it(
        self, monkeypatch: pytest.MonkeyPatch, crop_detect: bool
    ) -> None:
        # encode_concatenated_distorted applies crop only when cropdetect is
        # on. Framing the reference differently from the encode would score
        # two different pictures against each other.
        crop = CropValues(left=0, right=0, top=140, bottom=140)
        captured: dict[str, object] = {}

        def fake(**kwargs: object) -> str:
            captured.update(kwargs)
            return ""

        monkeypatch.setattr(encoding_utils, "build_sampling_vpy_script", fake)

        _ = build_reference_script(_ctx(crop_detect=crop_detect, crop=crop), _PARAMS)

        assert captured["crop_values"] == (crop if crop_detect else None)


class TestReferenceVideoInfo:
    """VMAF scales the distorted track to the reference's dimensions, and the
    reference is now a cropped clip rather than a file that can be probed.
    """

    def test_crop_comes_off_the_dimensions(self) -> None:
        crop = CropValues(left=0, right=0, top=140, bottom=140)

        info = reference_video_info(_ctx(crop_detect=True, crop=crop))

        assert (info.width, info.height) == (3840, 1880)

    def test_without_cropdetect_the_source_dimensions_stand(self) -> None:
        crop = CropValues(left=0, right=0, top=140, bottom=140)

        info = reference_video_info(_ctx(crop_detect=False, crop=crop))

        assert (info.width, info.height) == (3840, 2160)

    def test_everything_but_the_dimensions_is_the_source(self) -> None:
        # The frames are the source's own, so its colour tags describe them.
        crop = CropValues(left=20, right=20, top=0, bottom=0)
        ctx = _ctx(crop_detect=True, crop=crop)

        info = reference_video_info(ctx)

        assert info == replace(ctx.info, width=3800)
