from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from videotuner.constants import CROPDETECT_MIN_SAMPLES
from videotuner.cropdetect import (
    build_cropdetect_vpy_script,
    cropdetect_sample_plan,
)
from videotuner.encoding_utils import IndexerType, VapourSynthEnv, write_vpy_script
from videotuner.media import VideoInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
VS_DIR = REPO_ROOT / "vapoursynth-portable"
VS_PYTHON = VS_DIR / "python.exe"
VS_PLUGINS = VS_DIR / "vs-plugins"

_SDR = VideoInfo(
    fps=24.0,
    duration=1.0,
    color_trc="bt709",
    color_primaries="bt709",
    color_space="bt709",
)

_PQ = VideoInfo(
    fps=24.0,
    duration=1.0,
    color_trc="smpte2084",
    color_primaries="bt2020",
    color_space="bt2020nc",
    mastering_display_color_primaries="BT.2020",
    mastering_display_luminance="min: 0.0001 cd/m2, max: 1000 cd/m2",
)

_PQ_NO_MASTERING = VideoInfo(
    fps=24.0,
    duration=1.0,
    color_trc="smpte2084",
    color_primaries="bt2020",
    color_space="bt2020nc",
)

_HLG = VideoInfo(
    fps=24.0,
    duration=1.0,
    color_trc="arib-std-b67",
    color_primaries="bt2020",
    color_space="bt2020nc",
)


def _script(info: VideoInfo, indexer: IndexerType = IndexerType.FFMS2) -> str:
    plan = cropdetect_sample_plan(
        start_frame=0, num_frames=10_000, fps=24.0, interval=30
    )
    assert plan is not None
    return build_cropdetect_vpy_script(
        source_path=Path("source.mkv"),
        cache_file=Path("source.ffindex"),
        indexer=indexer,
        info=info,
        plan=plan,
    )


class TestSamplePlan:
    """The frames chosen, and the floor under how many there are."""

    @pytest.mark.parametrize(
        ("num_frames", "fps", "interval"),
        [
            (10_000, 24.0, 30),
            (100_000, 25.0, 30),
            (10_001, 24.0, 30),
            (7_777, 23.976, 17),
            (50_000, 30.0, 60),
        ],
    )
    def test_it_picks_the_same_frames_as_the_old_range(
        self, num_frames: int, fps: float, interval: int
    ) -> None:
        # The old code built range(safe_start, safe_end, step) with
        # step = max(1, round(fps * interval)). On a source long enough that
        # the floor does not bite, SelectEvery must land on exactly those
        # frame numbers, or the crop is measured on different pictures for
        # reasons nobody chose.
        #
        # The old step is recomputed here rather than read back from the
        # plan. Reading it back would compare the plan against itself.
        skip = max(1, int(num_frames * 0.10))
        old_step = max(1, round(fps * interval))
        expected = list(range(skip, num_frames - skip, old_step))
        assert len(expected) >= CROPDETECT_MIN_SAMPLES, (
            "this case is short enough for the floor to bite, so it does "
            "not test what it claims to"
        )

        plan = cropdetect_sample_plan(
            start_frame=0, num_frames=num_frames, fps=fps, interval=interval
        )
        assert plan is not None
        assert plan.step == old_step
        assert list(range(plan.start, plan.end, plan.step)) == expected

    def test_the_count_matches_the_frames_it_describes(self) -> None:
        # pipeline.py sets its progress total from count. If count and the
        # script disagree the bar either stalls or finishes early.
        plan = cropdetect_sample_plan(
            start_frame=0, num_frames=10_001, fps=24.0, interval=30
        )
        assert plan is not None
        assert plan.count == len(range(plan.start, plan.end, plan.step))

    @pytest.mark.parametrize(
        ("num_frames", "old_samples", "new_step"),
        [(1000, 2, 160), (500, 1, 80), (100, 1, 16)],
    )
    def test_a_short_source_still_gets_five_samples(
        self, num_frames: int, old_samples: int, new_step: int
    ) -> None:
        # Without the floor a short source is judged on one frame, and a
        # single dark or bright picture sets the crop for every encode.
        skip = max(1, int(num_frames * 0.10))
        old_step = max(1, round(24.0 * 30))
        assert len(range(skip, num_frames - skip, old_step)) == old_samples

        plan = cropdetect_sample_plan(
            start_frame=0, num_frames=num_frames, fps=24.0, interval=30
        )
        assert plan is not None
        assert plan.step == new_step
        assert plan.count == CROPDETECT_MIN_SAMPLES

    def test_a_long_source_is_not_forced_to_the_floor(self) -> None:
        # The floor lowers the step; it must never raise it.
        plan = cropdetect_sample_plan(
            start_frame=0, num_frames=1_000_000, fps=24.0, interval=30
        )
        assert plan is not None
        assert plan.step == 720
        assert plan.count > CROPDETECT_MIN_SAMPLES

    def test_a_clip_shorter_than_the_floor_takes_every_frame(self) -> None:
        plan = cropdetect_sample_plan(
            start_frame=0, num_frames=8, fps=24.0, interval=30
        )
        assert plan is not None
        assert plan.step == 1

    def test_no_usable_range_yields_no_plan(self) -> None:
        # The caller warns and returns a zero crop. Returning a plan with no
        # frames would instead build a script vspipe rejects.
        assert (
            cropdetect_sample_plan(start_frame=0, num_frames=2, fps=24.0, interval=30)
            is None
        )

    def test_the_start_frame_offsets_the_whole_plan(self) -> None:
        plan = cropdetect_sample_plan(
            start_frame=500, num_frames=10_000, fps=24.0, interval=30
        )
        assert plan is not None
        assert plan.start == 500 + 1000
        assert plan.end == 500 + 10_000 - 1000


class TestScript:
    """What reaches vspipe."""

    @pytest.mark.parametrize(
        "info",
        [_SDR, _PQ, _HLG, _PQ_NO_MASTERING],
        ids=["sdr", "pq", "hlg", "pq_no_mastering"],
    )
    def test_the_script_is_valid_python(self, info: VideoInfo) -> None:
        # The script is a formatted string, so a syntax error passes every
        # other test and fails only at runtime.
        compile(_script(info), "cropdetect.vpy", "exec")

    @pytest.mark.parametrize(
        "info",
        [_SDR, _PQ, _HLG, _PQ_NO_MASTERING],
        ids=["sdr", "pq", "hlg", "pq_no_mastering"],
    )
    def test_every_path_emits_eight_bit(self, info: VideoInfo) -> None:
        # ffmpeg no longer carries format=yuv420p, so the script is the only
        # thing keeping --cropdetect-limit in the units it documents.
        assert "format=vs.YUV420P8" in _script(info)

    def test_it_loads_through_the_selected_indexer(self) -> None:
        ffms2 = _script(_SDR, IndexerType.FFMS2)
        assert "core.ffms2.Source(" in ffms2
        assert "core.lsmas.LWLibavSource(" not in ffms2

        lsmash = _script(_SDR, IndexerType.LSMASH)
        assert "core.lsmas.LWLibavSource(" in lsmash
        assert "core.ffms2.Source(" not in lsmash

    def test_an_sdr_input_does_not_tonemap(self) -> None:
        assert "core.tonemap." not in _script(_SDR)

    def test_a_pq_input_tonemaps(self) -> None:
        src = _script(_PQ)
        assert "core.tonemap.BT2390(" in src
        assert "core.tonemap.BT2407(" in src

    def test_it_trims_then_selects(self) -> None:
        # Selecting first and trimming second would pick different frames.
        src = _script(_SDR)
        assert src.index("[1000:9000]") < src.index("SelectEvery")

    def test_it_registers_exactly_one_output(self) -> None:
        # ffmpeg reads y4m from a single stdout. A second output would be
        # written into the same stream and read as trailing garbage.
        assert _script(_SDR).count(".set_output(") == 1


requires_vapoursynth = pytest.mark.skipif(
    not VS_PYTHON.exists() or not VS_PLUGINS.exists(),
    reason="vapoursynth-portable is not provisioned; run tools/update_vs.py",
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is not on PATH",
)


@requires_vapoursynth
@requires_ffmpeg
def test_vspipe_writes_the_frames_the_plan_describes(tmp_path: Path) -> None:
    # The only claim the string tests cannot make: that the script vspipe
    # actually loads yields the promised count and depth.
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
            "testsrc2=size=64x64:rate=24:duration=2",
            "-pix_fmt",
            "yuv420p10le",
            str(source),
        ],
        capture_output=True,
        check=True,
    )

    plan = cropdetect_sample_plan(start_frame=0, num_frames=48, fps=24.0, interval=1)
    assert plan is not None

    script = tmp_path / "cropdetect.vpy"
    write_vpy_script(
        script,
        build_cropdetect_vpy_script(
            source_path=source,
            cache_file=tmp_path / "source.ffindex",
            indexer=IndexerType.FFMS2,
            info=_SDR,
            plan=plan,
        ),
    )

    vs_env = VapourSynthEnv.from_cwd(REPO_ROOT)
    proc = subprocess.run(
        [str(vs_env.vspipe_bin), "-i", str(script), "-"],
        capture_output=True,
        text=True,
        env=vs_env.build_env(),
        check=False,
    )
    assert proc.returncode == 0, f"vspipe rejected the script:\n{proc.stderr}"

    fields = dict(
        (key.strip(), value.strip())
        for key, _, value in (line.partition(":") for line in proc.stdout.splitlines())
    )
    # 10-bit in, 8-bit out: the conversion is the point.
    assert fields["Format Name"] == "YUV420P8"
    assert fields["Frames"] == str(plan.count)
