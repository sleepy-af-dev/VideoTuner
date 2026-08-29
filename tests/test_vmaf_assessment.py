"""Tests for vmaf_assessment build_vmaf_filter with GPU/CPU branching."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from videotuner import vmaf_assessment
from videotuner.media import VideoInfo
from videotuner.vmaf_assessment import build_vmaf_filter, run_vmaf


class TestBuildVmafFilter:
    """Tests for build_vmaf_filter GPU/CPU branching."""

    def test_tonemap_gpu_uses_libplacebo(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=True,
            _dis_needs_tonemap=True,
            use_gpu=True,
        )
        assert "libplacebo=" in result

    def test_tonemap_cpu_uses_zscale(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=True,
            _dis_needs_tonemap=True,
            use_gpu=False,
        )
        assert "zscale=" in result
        assert "tonemap=hable" in result

    def test_tonemap_cpu_no_libplacebo(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=True,
            _dis_needs_tonemap=True,
            use_gpu=False,
        )
        assert "libplacebo" not in result

    def test_tonemap_off_ignores_gpu(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=True,
            _dis_needs_tonemap=True,
            tonemap_policy="off",
            use_gpu=True,
        )
        assert "libplacebo" not in result
        assert "scale=" in result

    def test_no_tonemap_uses_scale(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=False,
            _dis_needs_tonemap=False,
            use_gpu=True,
        )
        assert "scale=" in result
        assert "libplacebo" not in result

    def test_default_use_gpu_true(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=True,
            _dis_needs_tonemap=True,
        )
        # Default use_gpu=True should produce libplacebo
        assert "libplacebo=" in result

    def test_filter_graph_structure(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=True,
            _dis_needs_tonemap=True,
            use_gpu=True,
        )
        assert "[0:v]" in result
        assert "[1:v]" in result
        assert "[ref]" in result
        assert "[dis]" in result
        assert "[dis][ref]" in result

    def test_tonemap_force_policy(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=False,
            _dis_needs_tonemap=False,
            tonemap_policy="force",
            use_gpu=True,
        )
        assert "libplacebo=" in result

    def test_tonemap_force_cpu(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=False,
            _dis_needs_tonemap=False,
            tonemap_policy="force",
            use_gpu=False,
        )
        assert "tonemap=hable" in result
        assert "libplacebo" not in result

    def test_pixel_format_8bit(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=False,
            _dis_needs_tonemap=False,
            dis_bit_depth=8,
        )
        assert "format=yuv420p," in result

    def test_pixel_format_10bit(self) -> None:
        result = build_vmaf_filter(
            ref_needs_tonemap=False,
            _dis_needs_tonemap=False,
            dis_bit_depth=10,
        )
        assert "format=yuv420p10le," in result


class TestVmafLogPathLength:
    """libvmaf writes its JSON with fopen, which is capped at MAX_PATH on Windows.

    It treats the failure as non-fatal, so ffmpeg still exits 0 and the only
    symptom is NaN scores. Job folders nested under a batch folder can exceed
    260 characters, so the filter must always be handed a short path.
    """

    @staticmethod
    def _info() -> VideoInfo:
        return VideoInfo(fps=24.0, duration=1.0, width=1920, height=1080)

    def _run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: Path
    ) -> str:
        """Invoke run_vmaf with a stubbed ffmpeg; return the log_path it asked for."""
        captured: dict[str, str] = {}

        def fake_run(cmd: list[str], **_kwargs: object) -> None:
            graph = cmd[cmd.index("-filter_complex") + 1]
            match = re.search(r"log_path=([^:]+)", graph)
            assert match is not None
            captured["log_path"] = match.group(1)
            # Stand in for libvmaf writing its log to the path it was given.
            written = tmp_path / captured["log_path"]
            _ = written.parent.mkdir(parents=True, exist_ok=True)
            _ = written.write_text(
                json.dumps({"pooled_metrics": {"vmaf": {"mean": 95.0}}}),
                encoding="utf-8",
            )

        monkeypatch.setattr(vmaf_assessment, "run", fake_run)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = run_vmaf(
            ffmpeg_bin="ffmpeg",
            ref_path=tmp_path / "ref.mkv",
            dis_path=tmp_path / "dis.mkv",
            ref_info=self._info(),
            dis_info=self._info(),
            log_path=destination,
            cwd=tmp_path,
        )
        return captured["log_path"]

    def test_filter_gets_a_short_path_even_for_a_long_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deep = tmp_path
        while len(str(deep)) < 240:
            deep = deep / ("nested_" + "x" * 24)
        destination = deep / "vmaf_concatenated_iter1.json"
        assert len(str(destination)) > 260, "fixture must exceed MAX_PATH"

        asked_for = self._run(tmp_path, monkeypatch, destination)

        assert len(str(tmp_path / asked_for)) < 260
        assert destination.exists(), "result must be moved to the long destination"

    def test_temp_dir_is_cleaned_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        destination = tmp_path / "out" / "vmaf.json"

        _ = self._run(tmp_path, monkeypatch, destination)

        assert destination.exists()
        assert not list(tmp_path.glob(".vt_vmaf_*"))
