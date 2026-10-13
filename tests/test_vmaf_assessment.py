"""Tests for vmaf_assessment: model selection, options, and run_vmaf plumbing.

build_vmaf_filter's own tests moved to test_vmaf_pipe.py, since it no longer
does any tonemapping or GPU/CPU branching.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from videotuner import vmaf_assessment
from videotuner.encoding_utils import (
    IndexerType,
    SampledSource,
    UsableRange,
    VapourSynthEnv,
    build_sampling_vpy_script,
    distorted_index_path,
)
from videotuner.media import VideoInfo
from videotuner.vmaf_assessment import (
    build_libvmaf_model_option,
    needs_tonemap,
    resolve_vmaf_choice,
    run_vmaf,
    select_vmaf_model,
    vmaf_model_load_error,
    vmaf_v1_available,
)
from videotuner.vmaf_script import build_vmaf_vpy_script
from videotuner.vmaf_types import VmafFamily

# The reference half of every script run_vmaf builds below, composed the way
# the pipeline composes it. These tests stub out the subprocess, so it is
# never executed; building it properly keeps the call sites honest about what
# run_vmaf is handed.
_SAMPLING = build_sampling_vpy_script(
    sources=[
        SampledSource(
            path=Path("source.mkv"),
            cache_file=Path("source.ffindex"),
            usable_range=UsableRange(start=0, end=240, frame_count=240),
        )
    ],
    interval_frames=24,
    region_frames=2,
    fps=24.0,
    indexer=IndexerType.FFMS2,
)


class TestSelectVmafModel:
    """Model names are chosen by display and viewing distance, not by source
    resolution. Every name here was confirmed to load as a libvmaf built-in.
    """

    def test_default_is_the_v1_living_room_model(self) -> None:
        assert select_vmaf_model(1920, 1080, fps=24.0) == "vmaf_v1.0.16_3d0h"

    def test_2160p_uses_the_v1_uhd_model(self) -> None:
        assert select_vmaf_model(3840, 2160, fps=24.0) == "vmaf_v1.0.16_1d5h_2160"

    def test_high_frame_rate_takes_the_hfr_variant(self) -> None:
        """hfr is an infix, not a suffix."""
        assert select_vmaf_model(1920, 1080, fps=60.0) == "vmaf_v1.0.16_hfr_3d0h"
        assert select_vmaf_model(3840, 2160, fps=60.0) == "vmaf_v1.0.16_hfr_1d5h_2160"

    def test_hfr_threshold_splits_broadcast_rates_from_film_rates(self) -> None:
        """47 separates 47.952/48/50/59.94/60 from 24/25/30, and the boundary
        itself counts as high frame rate."""
        assert "_hfr_" not in select_vmaf_model(1920, 1080, fps=30.0)
        assert "_hfr_" not in select_vmaf_model(1920, 1080, fps=46.9)
        assert "_hfr_" in select_vmaf_model(1920, 1080, fps=47.0)
        assert "_hfr_" in select_vmaf_model(1920, 1080, fps=47.952)

    def test_unknown_frame_rate_is_not_treated_as_high(self) -> None:
        assert select_vmaf_model(1920, 1080, fps=None) == "vmaf_v1.0.16_3d0h"
        assert select_vmaf_model(1920, 1080, fps=0.0) == "vmaf_v1.0.16_3d0h"

    def test_v0_returns_the_legacy_models(self) -> None:
        assert select_vmaf_model(1920, 1080, fps=24.0, use_v0=True) == "vmaf_v0.6.1"
        assert select_vmaf_model(3840, 2160, fps=24.0, use_v0=True) == "vmaf_4k_v0.6.1"

    def test_v0_has_no_high_frame_rate_variant(self) -> None:
        """The v0 generation ships no hfr models, so the frame rate cannot
        change the answer."""
        assert select_vmaf_model(1920, 1080, fps=60.0, use_v0=True) == "vmaf_v0.6.1"
        assert select_vmaf_model(3840, 2160, fps=60.0, use_v0=True) == "vmaf_4k_v0.6.1"


class TestBuildLibvmafModelOption:
    """The override separator is a footgun.

    ``:`` is also the filter-option delimiter, so libvmaf's model-config
    overrides need it escaped with two literal backslashes. Verified against
    ffmpeg: a bare colon and a single backslash both fail with "Error applying
    option 'cambi.enc_bitdepth'". ``|`` is worse than failing, because it exits
    0 while swallowing every option after it, including log_path, so no log is
    written and the run looks fine.
    """

    def test_version_model_without_override(self) -> None:
        assert (
            build_libvmaf_model_option("vmaf_v1.0.16_3d0h")
            == "model=version=vmaf_v1.0.16_3d0h"
        )

    def test_cambi_override_uses_two_literal_backslashes(self) -> None:
        result = build_libvmaf_model_option("vmaf_v1.0.16_3d0h", enc_bitdepth=8)
        assert result == (
            "model=version=vmaf_v1.0.16_3d0h" + "\\\\" + ":cambi.enc_bitdepth=8"
        )
        # Spelled out, because the count is the whole point
        assert result.count("\\") == 2
        assert "|" not in result

    def test_override_carries_the_encode_depth_not_the_measure_depth(self) -> None:
        """CAMBI scales its banding detection by the depth the content was
        encoded at, which is the source depth, while measurement is always at
        10-bit."""
        assert "cambi.enc_bitdepth=10" in build_libvmaf_model_option(
            "vmaf_v1.0.16_3d0h", enc_bitdepth=10
        )


class TestVmafV1Available:
    """There is no way to ask FFmpeg which libvmaf it links.

    FFmpeg requires only libvmaf >= 2.0.0 and passes the model name through as
    an opaque string, holding no model list of its own. `-version` and
    `-buildconf` say nothing about it. So the only way to know whether the v1
    models exist is to try one and read the exit code.
    """

    def setup_method(self) -> None:
        vmaf_v1_available.cache_clear()

    def test_available_when_the_probe_succeeds(self) -> None:
        result = MagicMock()
        result.returncode = 0
        with patch("videotuner.vmaf_assessment.subprocess.run", return_value=result):
            assert vmaf_v1_available("ffmpeg") is True

    def test_unavailable_when_the_probe_fails(self) -> None:
        """An older libvmaf exits non-zero: measured as 127, with 'could not
        load libvmaf model with version'."""
        result = MagicMock()
        result.returncode = 127
        with patch("videotuner.vmaf_assessment.subprocess.run", return_value=result):
            assert vmaf_v1_available("ffmpeg") is False

    def test_missing_ffmpeg_is_unavailable(self) -> None:
        with patch(
            "videotuner.vmaf_assessment.subprocess.run", side_effect=FileNotFoundError
        ):
            assert vmaf_v1_available("nonexistent") is False

    def test_timeout_is_unavailable(self) -> None:
        with patch(
            "videotuner.vmaf_assessment.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30),
        ):
            assert vmaf_v1_available("ffmpeg") is False

    def test_probes_once_per_process(self) -> None:
        """It must not run per CRF iteration."""
        result = MagicMock()
        result.returncode = 0
        with patch(
            "videotuner.vmaf_assessment.subprocess.run", return_value=result
        ) as run:
            assert vmaf_v1_available("ffmpeg") is True
            assert vmaf_v1_available("ffmpeg") is True
            run.assert_called_once()

    def test_probes_large_enough_for_every_model(self) -> None:
        """Two independent minimum-size traps, both of which would silently
        downgrade every run by reporting a false negative. CAMBI needs 216 in
        both dimensions; SpEED chroma's floor varies by model, and the 4K@3H
        model needs 640x480. Anything smaller passes for some models and fails
        for others."""
        result = MagicMock()
        result.returncode = 0
        with patch(
            "videotuner.vmaf_assessment.subprocess.run", return_value=result
        ) as run:
            _ = vmaf_v1_available("ffmpeg")
            cmd = " ".join(cast(list[str], run.call_args[0][0]))
            assert "640x480" in cmd

    def test_probes_with_a_v1_model(self) -> None:
        result = MagicMock()
        result.returncode = 0
        with patch(
            "videotuner.vmaf_assessment.subprocess.run", return_value=result
        ) as run:
            _ = vmaf_v1_available("ffmpeg")
            cmd = " ".join(cast(list[str], run.call_args[0][0]))
            assert "vmaf_v1.0.16" in cmd


class TestResolveVmafChoice:
    """A silent fallback is the failure that matters here.

    v1 scores are calibrated to the v0 scale, so v0 numbers produced by an
    unnoticed fallback look exactly like v1 numbers and compare cleanly against
    a v1 target. The fallback has to be visible or it is worse than an error.
    """

    def test_v1_when_the_probe_finds_it(self) -> None:
        choice = resolve_vmaf_choice(None, use_v0=False, v1_available=True)
        assert choice.family is VmafFamily.V1
        assert choice.fell_back is False
        assert choice.explicit_model is None

    def test_falls_back_to_v0_and_says_so(self) -> None:
        choice = resolve_vmaf_choice(None, use_v0=False, v1_available=False)
        assert choice.family is VmafFamily.V0
        assert choice.fell_back is True

    def test_v0_flag_wins_without_consulting_the_probe(self) -> None:
        """The probe cannot change this answer, so callers skip it."""
        choice = resolve_vmaf_choice(None, use_v0=True, v1_available=False)
        assert choice.family is VmafFamily.V0
        assert choice.fell_back is False

    def test_explicit_model_is_carried_through(self) -> None:
        choice = resolve_vmaf_choice(
            "vmaf_v1.0.16_5d0h", use_v0=False, v1_available=True
        )
        assert choice.explicit_model == "vmaf_v1.0.16_5d0h"
        assert choice.family is VmafFamily.V1

    def test_explicit_v0_model_is_a_legitimate_choice(self) -> None:
        """Naming a v0 model is a deliberate request, not a fallback, so it
        carries no warning."""
        choice = resolve_vmaf_choice("vmaf_v0.6.1", use_v0=False, v1_available=True)
        assert choice.family is VmafFamily.V0
        assert choice.fell_back is False

    def test_explicit_model_needs_no_probe(self) -> None:
        """A named model is used as given, so a failed probe is irrelevant and
        must not silently substitute something else."""
        choice = resolve_vmaf_choice(
            "vmaf_v1.0.16_3d0h", use_v0=False, v1_available=False
        )
        assert choice.explicit_model == "vmaf_v1.0.16_3d0h"
        assert choice.fell_back is False

    def test_v0_flag_with_an_explicit_v1_model_is_a_contradiction(self) -> None:
        """Picking a winner would silently ignore half of what was asked."""
        with pytest.raises(ValueError, match="v0-vmaf"):
            _ = resolve_vmaf_choice("vmaf_v1.0.16_3d0h", use_v0=True, v1_available=True)

    def test_v0_flag_with_an_explicit_v0_model_agrees(self) -> None:
        choice = resolve_vmaf_choice("vmaf_v0.6.1", use_v0=True, v1_available=False)
        assert choice.family is VmafFamily.V0
        assert choice.explicit_model == "vmaf_v0.6.1"


class TestVmafModelLoadError:
    """A named model that does not exist fails inside the filtergraph.

    run_vspipe_encode() streams output rather than capturing it, so the
    RuntimeError it raises carries only an exit status. Without reading the
    line as it goes past, the user is told a command failed with code 127 and
    never which model was wrong.
    """

    def test_detects_the_model_load_failure(self) -> None:
        # Verbatim from ffmpeg, which exits 127 on this
        line = (
            "[Parsed_libvmaf_2 @ 00000195c9c55d00] could not load libvmaf model"
            " with version: vmaf_definitely_not_a_model"
        )
        assert vmaf_model_load_error(line) == "vmaf_definitely_not_a_model"

    def test_ignores_ordinary_progress(self) -> None:
        assert vmaf_model_load_error("frame=  123 fps=45 q=-0.0 size=N/A") is None

    def test_ignores_an_unrelated_filter_error(self) -> None:
        line = "[AVFilterGraph @ 000001] Error initializing filters"
        assert vmaf_model_load_error(line) is None


class TestNeedsTonemap:
    """The values here are what ffprobe reports, verbatim.

    parse_video_info fills these fields from ffprobe's JSON output, which names
    transfers and primaries in lowercase with no punctuation. A fixture written
    in any other vocabulary tests a VideoInfo the pipeline never builds.
    """

    def test_pq_source_needs_tonemap(self) -> None:
        info = VideoInfo(
            fps=24.0,
            duration=100.0,
            color_trc="smpte2084",
            color_primaries="bt2020",
        )
        assert needs_tonemap(info) is True

    def test_hlg_source_needs_tonemap(self) -> None:
        info = VideoInfo(
            fps=24.0,
            duration=100.0,
            color_trc="arib-std-b67",
            color_primaries="bt2020",
        )
        assert needs_tonemap(info) is True

    def test_bt709_source_does_not(self) -> None:
        info = VideoInfo(
            fps=24.0, duration=100.0, color_trc="bt709", color_primaries="bt709"
        )
        assert needs_tonemap(info) is False

    def test_wide_gamut_sdr_does_not(self) -> None:
        """A BT.2020 source with an SDR transfer wants gamut conversion, not tone
        mapping. Keying on primaries would tonemap it and crush nothing."""
        info = VideoInfo(
            fps=24.0,
            duration=100.0,
            color_trc="bt2020-10",
            color_primaries="bt2020",
        )
        assert needs_tonemap(info) is False

    def test_untagged_source_does_not(self) -> None:
        """ffprobe omits colour fields it has no value for, rather than naming
        them unknown, so None is the normal case for an untagged file."""
        info = VideoInfo(fps=24.0, duration=100.0)
        assert needs_tonemap(info) is False


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

        def fake_run_vspipe_encode(encoder_args: list[str], **_kwargs: object) -> str:
            graph = encoder_args[encoder_args.index("-filter_complex") + 1]
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
            return ""

        monkeypatch.setattr(
            vmaf_assessment, "run_vspipe_encode", fake_run_vspipe_encode
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = run_vmaf(
            ffmpeg_bin="ffmpeg",
            ref_script=_SAMPLING,
            dis_path=tmp_path / "dis.mkv",
            ref_info=self._info(),
            dis_info=self._info(),
            log_path=destination,
            cwd=tmp_path,
            indexer=IndexerType.FFMS2,
            vs_env=VapourSynthEnv.from_cwd(tmp_path),
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


def _fake_captured_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """Stub run_vspipe_encode and capture the -filter_complex it was given.

    Shared by the tests below, which only care about the emitted graph, not
    about a real libvmaf log ever appearing.
    """
    captured: dict[str, str] = {}

    def fake_run_vspipe_encode(encoder_args: list[str], **_kwargs: object) -> str:
        captured["graph"] = encoder_args[encoder_args.index("-filter_complex") + 1]
        return ""

    monkeypatch.setattr(vmaf_assessment, "run_vspipe_encode", fake_run_vspipe_encode)
    return captured


class TestRunVmafScalesToTheReference:
    """build_vmaf_filter only scales the distorted track, so run_vmaf has to
    hand it the *reference's* dimensions. Handing it the distorted encode's
    own (usually smaller) size makes the scale a no-op and leaves libvmaf two
    differently sized tracks whenever the encode was downscaled.
    """

    def test_scale_targets_the_reference_not_the_distorted_encode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _fake_captured_graph(monkeypatch)

        _ = run_vmaf(
            ffmpeg_bin="ffmpeg",
            ref_script=_SAMPLING,
            dis_path=tmp_path / "dis.mkv",
            ref_info=VideoInfo(fps=24.0, duration=1.0, width=3840, height=2160),
            dis_info=VideoInfo(fps=24.0, duration=1.0, width=1920, height=1080),
            log_path=tmp_path / "vmaf.json",
            cwd=tmp_path,
            indexer=IndexerType.FFMS2,
            vs_env=VapourSynthEnv.from_cwd(tmp_path),
        )

        assert "scale=3840:2160:flags=bicubic" in captured["graph"]


class TestRunVmafKeepsTheSharedDistortedIndex:
    """One index per distorted encode, shared with SSIMULACRA2 rather than
    deleted after use.

    In shared-sampling mode both metrics score the same file. Deleting the
    index here would make the other metric rebuild it, and a ten-iteration
    search would pay that ten times. Nothing goes stale: the encode's own
    name carries its CRF and iteration, so an index beside it can only ever
    describe that file. The reference has no index of its own — it is the
    sampling script, reading the source through the job's own indexes.
    """

    def test_the_index_is_left_for_the_other_metric(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _ = _fake_captured_graph(monkeypatch)
        dis_path = tmp_path / "shared_distorted_crf28.0_iter1.mkv"
        dis_cache = distorted_index_path(dis_path, IndexerType.FFMS2)
        # Stand in for an index this run's own indexing step just built.
        _ = dis_cache.write_text("distorted index", encoding="utf-8")

        _ = run_vmaf(
            ffmpeg_bin="ffmpeg",
            ref_script=_SAMPLING,
            dis_path=dis_path,
            ref_info=VideoInfo(fps=24.0, duration=1.0, width=1920, height=1080),
            dis_info=VideoInfo(fps=24.0, duration=1.0, width=1920, height=1080),
            log_path=tmp_path / "vmaf.json",
            cwd=tmp_path,
            indexer=IndexerType.FFMS2,
            vs_env=VapourSynthEnv.from_cwd(tmp_path),
        )

        assert dis_cache.exists(), (
            "SSIMULACRA2 scores the same file and would have to rebuild it"
        )

    def test_the_script_reads_the_shared_index_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The path in the generated script must be the one SSIMULACRA2 also
        # derives, or each metric builds its own under a different name and
        # the sharing is nominal.
        _ = _fake_captured_graph(monkeypatch)
        dis_path = tmp_path / "shared_distorted_crf28.0_iter1.mkv"

        _ = run_vmaf(
            ffmpeg_bin="ffmpeg",
            ref_script=_SAMPLING,
            dis_path=dis_path,
            ref_info=VideoInfo(fps=24.0, duration=1.0, width=1920, height=1080),
            dis_info=VideoInfo(fps=24.0, duration=1.0, width=1920, height=1080),
            log_path=tmp_path / "vmaf.json",
            cwd=tmp_path,
            indexer=IndexerType.FFMS2,
            vs_env=VapourSynthEnv.from_cwd(tmp_path),
        )

        expected = distorted_index_path(dis_path, IndexerType.FFMS2)
        assert expected.name in build_vmaf_vpy_script(
            ref_script=_SAMPLING,
            dis_path=dis_path,
            dis_cache=expected,
            indexer=IndexerType.FFMS2,
            info=VideoInfo(fps=24.0, duration=1.0),
        )
