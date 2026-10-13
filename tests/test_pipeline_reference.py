"""Tests for pipeline_reference, and for the reference no longer being a file."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

from videotuner import pipeline_reference
from videotuner.pipeline import run_pipeline
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
    #
    # On its own this only says one name is absent: reference generation
    # reintroduced under any other name passes it. The run below is what
    # actually holds the line.
    assert not hasattr(pipeline_reference, "generate_metric_reference")


def test_no_iteration_can_be_pointed_at_a_reference_file() -> None:
    # The same shape of claim about the field a path would travel through,
    # and the same limitation.
    fields = {f.name for f in dataclasses.fields(IterationContext)}
    assert "vmaf_ref_path" not in fields
    assert "ssim2_ref_path" not in fields


REPO_ROOT = Path(__file__).resolve().parents[1]
VS_PYTHON = REPO_ROOT / "vapoursynth-portable" / "python.exe"
VS_PLUGINS = REPO_ROOT / "vapoursynth-portable" / "vs-plugins"
X265_BIN = REPO_ROOT / "tools" / "x265.exe"


def _ffmpeg_lists(kind: str, needle: str) -> bool:
    """Ask the ffmpeg on PATH whether it carries something this run needs.

    Args:
        kind: The listing to read, ``"encoders"`` or ``"filters"``.
        needle: Name to look for in it.

    Returns:
        True when ffmpeg runs and its listing mentions the name.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", f"-{kind}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return needle in proc.stdout


requires_a_real_run = pytest.mark.skipif(
    not VS_PYTHON.exists()
    or not VS_PLUGINS.exists()
    or not X265_BIN.exists()
    or shutil.which("mkvmerge") is None
    or not _ffmpeg_lists("encoders", "libx265")
    or not _ffmpeg_lists("filters", "libvmaf"),
    reason="a real run needs VapourSynth, x265, mkvmerge and ffmpeg with libvmaf",
)


@requires_a_real_run
def test_a_run_writes_no_reference_and_still_scores_both_metrics(
    tmp_path: Path,
) -> None:
    # The check the two above cannot make. Reference generation reintroduced
    # under any name puts a file in the job folder and this fails, whatever
    # the function is called; and demanding both scores from the same run
    # rules out passing by having quietly dropped a metric instead.
    source = tmp_path / "source.mkv"
    _ = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=8",
            "-pix_fmt",
            "yuv420p10le",
            "-c:v",
            "libx265",
            "-x265-params",
            "lossless=1:colorprim=bt709:transfer=bt709"
            + ":colormatrix=bt709:repeat-headers=1",
            str(source),
        ],
        capture_output=True,
        check=True,
    )

    workdir = tmp_path / "jobs"
    result = run_pipeline(
        PipelineArgs(
            input=source,
            workdir=workdir,
            encoder="x265",
            preset="ultrafast",
            assessment_only=True,
            crf_start_value=32.0,
            # Both metrics sample identically, so one encode serves both and
            # the run exercises the shared-sample path.
            vmaf_interval_frames=48,
            vmaf_region_frames=4,
            ssim2_interval_frames=48,
            ssim2_region_frames=4,
            crop_detect=False,
        ),
        show_title=False,
    )

    assert result.ok, result.status

    written = sorted(
        path.relative_to(workdir).as_posix()
        for path in workdir.rglob("*")
        if path.is_file()
    )
    # Every name the reference ever went by, plus the raw bitstreams it was
    # muxed from, anywhere in the tree rather than in the folder it used to
    # live in - a reference written somewhere else is still a reference.
    assert [name for name in written if "reference" in name.lower()] == [], written
    assert [
        name for name in written if name.endswith((".hevc", ".265", ".h264"))
    ] == [], written

    assert result.scores.get("vmaf_mean") is not None, result.scores
    assert result.scores.get("ssim2_mean") is not None, result.scores
