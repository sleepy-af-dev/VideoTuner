from __future__ import annotations

from pathlib import Path

from videotuner.encoding_utils import IndexerType
from videotuner.media import VideoInfo
from videotuner.vmaf_script import build_vmaf_vpy_script

_SDR = VideoInfo(
    fps=24.0,
    duration=1.0,
    color_trc="bt709",
    color_primaries="bt709",
    color_space="bt709",
)


def _script(info: VideoInfo, indexer: IndexerType = IndexerType.FFMS2) -> str:
    return build_vmaf_vpy_script(
        ref_path=Path("ref.mkv"),
        dis_path=Path("dis.mkv"),
        ref_cache=Path("ref.ffindex"),
        dis_cache=Path("dis.ffindex"),
        indexer=indexer,
        info=info,
    )


def test_the_script_is_valid_python() -> None:
    # The script is a formatted string, so a syntax error in it would pass
    # every other test and fail only at runtime.
    compile(_script(_SDR), "vmaf.vpy", "exec")


def test_the_reference_is_output_zero_and_distorted_is_output_one() -> None:
    # ffmpeg reads them as [0:v:0] and [0:v:1]. Swapping them silently scores
    # the reference against itself in one direction.
    src = _script(_SDR)
    assert "ref.set_output(0)" in src
    assert "dis.set_output(1)" in src


def test_both_clips_go_through_the_same_prepare() -> None:
    src = _script(_SDR)
    assert src.count("def prepare(") == 1
    assert "ref = prepare(ref)" in src
    assert "dis = prepare(dis)" in src


def test_the_script_loads_through_the_selected_indexer() -> None:
    assert "core.ffms2.Source(" in _script(_SDR, IndexerType.FFMS2)
    assert "core.lsmas.LWLibavSource(" in _script(_SDR, IndexerType.LSMASH)


def test_an_sdr_input_does_not_tonemap() -> None:
    assert "core.tonemap." not in _script(_SDR)


def test_a_pq_input_tonemaps() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        mastering_display_color_primaries="BT.2020",
        mastering_display_luminance="min: 0.0001 cd/m2, max: 1000 cd/m2",
    )
    src = _script(info)
    assert "core.tonemap.BT2390(" in src
    assert "core.tonemap.BT2407(" in src


def test_the_script_targets_vmaf_not_ssim2() -> None:
    assert "vs.YUV420P10" in _script(_SDR)
    assert "vszip" not in _script(_SDR)
