"""Tests for pipeline iteration module."""

import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from videotuner import (
    encoding_utils,
    pipeline_iteration,
    ssimulacra2_assessment,
    vmaf_assessment,
)
from videotuner.encoder_type import EncoderType
from videotuner.encoding_utils import CropValues, SampledSource, UsableRange
from videotuner.media import VideoInfo
from videotuner.pipeline_cli import PipelineArgs
from videotuner.pipeline_iteration import (
    MetricSampleParams,
    build_reference_script,
    calculate_metric_params,
    extract_scores,
    reference_video_info,
    run_assessments,
    run_combined_assessment,
)
from videotuner.pipeline_types import IterationContext
from videotuner.profiles import Profile
from videotuner.ssimulacra2_assessment import SSIM2Result
from videotuner.vmaf_assessment import VMAFResult


class TestMetricSampleParams:
    """Tests for MetricSampleParams dataclass."""

    def test_creation_with_all_fields(self):
        """Test that MetricSampleParams can be created with all fields."""
        params = MetricSampleParams(
            total_frames=2500,
            interval_frames=1000,
            region_frames=500,
        )
        assert params.total_frames == 2500
        assert params.interval_frames == 1000
        assert params.region_frames == 500

    def test_is_frozen(self):
        """Test that MetricSampleParams is immutable."""
        params = MetricSampleParams(
            total_frames=1500,
            interval_frames=500,
            region_frames=500,
        )
        with pytest.raises(AttributeError):
            setattr(params, "total_frames", 10)


def _sources_ctx(lengths: list[int], args: PipelineArgs) -> IterationContext:
    """A context holding one source for each usable length, and the args."""
    sources = [
        SampledSource(
            path=Path(f"part{i}.mkv"),
            cache_file=Path(f"part{i}.ffindex"),
            usable_range=UsableRange(start=0, end=length, frame_count=length),
        )
        for i, length in enumerate(lengths)
    ]
    return cast(
        IterationContext, cast(object, SimpleNamespace(sources=sources, args=args))
    )


class TestCalculateMetricParams:
    """Tests for calculate_metric_params function."""

    def test_calculates_vmaf_params(self):
        """Test that VMAF params are calculated correctly."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf_interval_frames=1000,
            vmaf_region_frames=500,
        )
        ctx = _sources_ctx([9000], args)

        params = calculate_metric_params(ctx, "vmaf")

        assert params.interval_frames == 1000
        assert params.region_frames == 500
        # 9 full regions of 500
        assert params.total_frames == 4500

    def test_calculates_ssim2_params(self):
        """Test that SSIM2 params are calculated correctly."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            ssim2_interval_frames=2000,
            ssim2_region_frames=300,
        )
        ctx = _sources_ctx([9000], args)

        params = calculate_metric_params(ctx, "ssim2")

        assert params.interval_frames == 2000
        assert params.region_frames == 300
        # 4 full regions of 300, and 1000 frames left hold a fifth
        assert params.total_frames == 1500

    def test_handles_small_usable_frames(self):
        """Test that calculation handles edge case of small usable frames."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf_interval_frames=500,
            vmaf_region_frames=300,
        )
        ctx = _sources_ctx([400], args)

        params = calculate_metric_params(ctx, "vmaf")

        assert params.total_frames == 300

    def test_counts_every_source(self):
        """Each file is sampled on its own, so each one adds its regions."""
        args = PipelineArgs(
            input=Path("test.mkv"),
            vmaf_interval_frames=1000,
            vmaf_region_frames=100,
        )

        params = calculate_metric_params(_sources_ctx([500, 500], args), "vmaf")

        assert params.total_frames == 200


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


_HDR = VideoInfo(
    fps=24.0,
    duration=10.0,
    width=3840,
    height=2160,
    color_trc="smpte2084",
    color_primaries="bt2020",
    color_space="bt2020nc",
)
_SDR = VideoInfo(fps=24.0, duration=10.0, width=3840, height=2160, color_trc="bt709")
_VMAF = VMAFResult(mean=95.0, harmonic_mean=94.0, minimum=90.0, p1_low=91.0)
_SSIM2 = SSIM2Result(
    mean=80.0, median=80.0, p5_low=70.0, p95_high=90.0, std_dev=1.0, count=20
)
_ENCODE = Path("shared.mkv")


def _routing_ctx(
    *,
    info: VideoInfo,
    sharing: bool = True,
    vmaf: bool = True,
    ssim2: bool = True,
) -> IterationContext:
    """A context carrying only what run_assessments reads."""
    return cast(
        IterationContext,
        cast(
            object,
            SimpleNamespace(
                info=info,
                sharing_samples=sharing,
                args=PipelineArgs(input=Path("test.mkv"), vmaf=vmaf, ssim2=ssim2),
                log=logging.getLogger("test_routing"),
                combined_pass_failed=False,
            ),
        ),
    )


class TestAssessmentRouting:
    """HDR with shared samples scores both metrics in one pass. Everything
    else, and any failure of that pass, takes the two separate passes."""

    @pytest.fixture
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        calls: list[str] = []

        def combined(
            _ctx: IterationContext, _path: Path, _iteration: int
        ) -> tuple[list[VMAFResult], list[SSIM2Result]]:
            calls.append("combined")
            return [_VMAF], [_SSIM2]

        def vmaf(
            _ctx: IterationContext, _path: Path, _iteration: int
        ) -> list[VMAFResult]:
            calls.append("vmaf")
            return [_VMAF]

        def ssim2(
            _ctx: IterationContext, _path: Path, _iteration: int
        ) -> list[SSIM2Result]:
            calls.append("ssim2")
            return [_SSIM2]

        monkeypatch.setattr(pipeline_iteration, "run_combined_assessment", combined)
        monkeypatch.setattr(pipeline_iteration, "run_vmaf_assessment", vmaf)
        monkeypatch.setattr(pipeline_iteration, "run_ssim2_assessment", ssim2)
        return calls

    def test_hdr_with_shared_samples_takes_one_pass(self, calls: list[str]) -> None:
        got = run_assessments(_routing_ctx(info=_HDR), _ENCODE, _ENCODE, 1)
        assert calls == ["combined"]
        assert got == ([_VMAF], [_SSIM2])

    def test_sdr_takes_the_separate_passes(self, calls: list[str]) -> None:
        _ = run_assessments(_routing_ctx(info=_SDR), _ENCODE, _ENCODE, 1)
        assert calls == ["vmaf", "ssim2"]

    def test_different_sampling_takes_the_separate_passes(
        self, calls: list[str]
    ) -> None:
        ctx = _routing_ctx(info=_HDR, sharing=False)
        _ = run_assessments(ctx, Path("vmaf.mkv"), Path("ssim2.mkv"), 1)
        assert calls == ["vmaf", "ssim2"]

    def test_one_metric_takes_its_own_pass(self, calls: list[str]) -> None:
        ctx = _routing_ctx(info=_HDR, ssim2=False)
        got = run_assessments(ctx, _ENCODE, None, 1)
        assert calls == ["vmaf"]
        assert got == ([_VMAF], [])

    def test_a_failed_pass_falls_back_and_says_so(
        self,
        calls: list[str],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def broken(
            _ctx: IterationContext, _path: Path, _iteration: int
        ) -> tuple[list[VMAFResult], list[SSIM2Result]]:
            calls.append("combined")
            raise RuntimeError("pipe broke")

        monkeypatch.setattr(pipeline_iteration, "run_combined_assessment", broken)

        got = run_assessments(_routing_ctx(info=_HDR), _ENCODE, _ENCODE, 2)

        assert calls == ["combined", "vmaf", "ssim2"]
        assert got == ([_VMAF], [_SSIM2])
        assert "iteration 2" in caplog.text
        assert "scoring each metric separately for the rest of this run" in caplog.text
        assert "pipe broke" in caplog.text

    def test_after_a_failed_pass_the_run_stops_trying_it(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A fault that repeats would otherwise cost a wasted pass and a
        # warning in every iteration.
        def broken(
            _ctx: IterationContext, _path: Path, _iteration: int
        ) -> tuple[list[VMAFResult], list[SSIM2Result]]:
            calls.append("combined")
            raise RuntimeError("pipe broke")

        monkeypatch.setattr(pipeline_iteration, "run_combined_assessment", broken)
        ctx = _routing_ctx(info=_HDR)

        _ = run_assessments(ctx, _ENCODE, _ENCODE, 1)
        _ = run_assessments(ctx, _ENCODE, _ENCODE, 2)

        assert calls == ["combined", "vmaf", "ssim2", "vmaf", "ssim2"]


_PROFILE = Profile(
    name="Example Profile (x265)",
    description="Example",
    settings={},
    encoder=EncoderType.X265,
)


class TestCombinedAssessment:
    """The combined pass checks that SSIMULACRA2 scored every frame VMAF
    scored, since a short list would summarise fewer frames."""

    @staticmethod
    def _run(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        frames: int,
        vmaf_frames: int = 20,
    ) -> tuple[list[VMAFResult], list[SSIM2Result]]:
        def no_index(**_kwargs: object) -> None:
            return None

        def fake_assess(**kwargs: object) -> list[VMAFResult]:
            scores = cast(list[float], kwargs["ssim2_scores"])
            scores.extend([80.0] * frames)
            return [replace(_VMAF, frame_scores=(95.0,) * vmaf_frames)]

        def params(_ctx: IterationContext, _metric: str) -> MetricSampleParams:
            return _PARAMS  # total_frames=20

        def script(_ctx: IterationContext, _params: MetricSampleParams) -> str:
            return "sampled = <selected>"

        monkeypatch.setattr(ssimulacra2_assessment, "build_distorted_index", no_index)
        monkeypatch.setattr(vmaf_assessment, "assess_vmaf_concatenated", fake_assess)
        monkeypatch.setattr(pipeline_iteration, "calculate_metric_params", params)
        monkeypatch.setattr(pipeline_iteration, "build_reference_script", script)
        ctx = cast(
            IterationContext,
            cast(
                object,
                SimpleNamespace(
                    args=PipelineArgs(input=Path("test.mkv"), crop_detect=False),
                    info=_HDR,
                    crop_values=None,
                    repo_root=tmp_path,
                    workdir=tmp_path,
                    selected_profile=_PROFILE,
                    display=None,
                    log=logging.getLogger("test_combined"),
                    vmaf_family=None,
                ),
            ),
        )
        return run_combined_assessment(ctx, tmp_path / "shared.mkv", 1)

    def test_every_frame_scored_gives_both_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vmaf, ssim2 = self._run(tmp_path, monkeypatch, frames=20)
        assert len(vmaf[0].frame_scores) == 20
        assert ssim2[0].count == 20

    def test_more_frames_than_the_sample_estimate_still_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # metric_params says 20 frames, but the pass is checked against what
        # both metrics actually scored, not against a computed total.
        vmaf, ssim2 = self._run(tmp_path, monkeypatch, frames=24, vmaf_frames=24)
        assert len(vmaf[0].frame_scores) == 24
        assert ssim2[0].count == 24

    def test_a_vmaf_pass_with_no_frame_scores_is_left_to_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # libvmaf writing no log gives a NaN result with no frame scores, as
        # in the separate pass. That is VMAF's failure, not a combined-pass
        # one: validate_assessment_results reports it, as it did in 0.7.0.
        vmaf, ssim2 = self._run(tmp_path, monkeypatch, frames=20, vmaf_frames=0)
        assert vmaf[0].frame_scores == ()
        assert ssim2[0].count == 20

    def test_a_missing_frame_fails_the_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(RuntimeError, match="19 of 20"):
            _ = self._run(tmp_path, monkeypatch, frames=19)
