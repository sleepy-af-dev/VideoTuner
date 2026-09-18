from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from videotuner.encoding_utils import (
    IndexerType,
    SampledSource,
    UsableRange,
    VapourSynthEnv,
    build_sampling_vpy_script,
    indexer_spec,
    write_vpy_script,
)
from videotuner.media import VideoInfo
from videotuner.vmaf_script import build_vmaf_vpy_script

REPO_ROOT = Path(__file__).resolve().parents[1]
VS_DIR = REPO_ROOT / "vapoursynth-portable"
VS_PYTHON = VS_DIR / "python.exe"
VS_PLUGINS = VS_DIR / "vs-plugins"

# Sampling that keeps two frames out of every twelve, so the reference is a
# selection rather than the whole clip.
_INTERVAL_FRAMES = 12
_REGION_FRAMES = 2
_SOURCE_FRAMES = 48

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

# Same as _PQ but with no mastering display, the branch where colour_preamble
# assumes src_max/src_min rather than reading them from the properties.
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


_DEFAULT_SOURCE = Path("source.mkv")


def _sampling(
    indexer: IndexerType = IndexerType.FFMS2,
    source: Path = _DEFAULT_SOURCE,
) -> str:
    """Build the reference half the way the pipeline does, by composing it."""
    suffix = indexer_spec(indexer).index_suffix
    return build_sampling_vpy_script(
        sources=[
            SampledSource(
                path=source,
                cache_file=source.with_suffix(suffix),
                usable_range=UsableRange(
                    start=0, end=_SOURCE_FRAMES, frame_count=_SOURCE_FRAMES
                ),
            )
        ],
        interval_frames=_INTERVAL_FRAMES,
        region_frames=_REGION_FRAMES,
        fps=24.0,
        indexer=indexer,
    )


def _script(info: VideoInfo, indexer: IndexerType = IndexerType.FFMS2) -> str:
    return build_vmaf_vpy_script(
        ref_script=_sampling(indexer),
        dis_path=Path("dis.mkv"),
        dis_cache=Path("dis.ffindex"),
        indexer=indexer,
        info=info,
    )


@pytest.mark.parametrize(
    "info",
    [_SDR, _PQ, _HLG, _PQ_NO_MASTERING],
    ids=["sdr", "pq", "hlg", "pq_no_mastering"],
)
def test_the_script_is_valid_python(info: VideoInfo) -> None:
    # The script is a formatted string, so a syntax error in it would pass
    # every other test and fail only at runtime. SDR, PQ, HLG and the
    # assumed-peak PQ variant each take a different path through
    # colour_preamble, so every one needs compiling.
    compile(_script(info), "vmaf.vpy", "exec")


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


def test_the_reference_is_selected_from_the_source_not_read_back() -> None:
    # The lossless copy is gone: the frames VMAF scores against are selected
    # by the same generator the encoder is fed, which is what makes the copy
    # redundant rather than merely expensive.
    src = _script(_SDR)
    assert "std.SelectEvery(" in src
    assert "ref = sampled" in src


def test_the_converted_reference_takes_output_zero_from_the_sampling_script() -> None:
    # The sampling script sets its own clip as output 0 and cannot be asked
    # not to. Registering the index again replaces it, so the order of the
    # two calls is what decides which clip ffmpeg reads.
    src = _script(_SDR)
    assert src.index("sampled.set_output()") < src.index("ref.set_output(0)")


def test_the_script_loads_through_the_selected_indexer() -> None:
    ffms2 = _script(_SDR, IndexerType.FFMS2)
    assert "core.ffms2.Source(" in ffms2
    assert "core.lsmas.LWLibavSource(" not in ffms2

    lsmash = _script(_SDR, IndexerType.LSMASH)
    assert "core.lsmas.LWLibavSource(" in lsmash
    assert "core.ffms2.Source(" not in lsmash


def test_an_sdr_input_does_not_tonemap() -> None:
    assert "core.tonemap." not in _script(_SDR)


def test_a_pq_input_tonemaps() -> None:
    src = _script(_PQ)
    assert "core.tonemap.BT2390(" in src
    assert "core.tonemap.BT2407(" in src


def test_the_script_targets_vmaf_not_ssim2() -> None:
    assert "vs.YUV420P10" in _script(_SDR)
    assert "vszip" not in _script(_SDR)


requires_vapoursynth = pytest.mark.skipif(
    not VS_PYTHON.exists() or not VS_PLUGINS.exists(),
    reason="vapoursynth-portable is not provisioned; run tools/update_vs.py",
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is not on PATH",
)


def _vspipe_outputs(script: Path) -> list[dict[str, str]]:
    """Ask vspipe what the script actually registered, per output index."""
    vs_env = VapourSynthEnv.from_cwd(REPO_ROOT)
    proc = subprocess.run(
        [str(vs_env.vspipe_bin), "-i", str(script), "-"],
        capture_output=True,
        text=True,
        env=vs_env.build_env(),
        check=False,
    )
    assert proc.returncode == 0, f"vspipe rejected the script:\n{proc.stderr}"

    outputs: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(":")
        if key == "Output Index":
            outputs.append({})
        elif outputs:
            outputs[-1][key] = value.strip()
    return outputs


@requires_vapoursynth
@requires_ffmpeg
def test_the_pair_vspipe_writes_is_the_converted_one(tmp_path: Path) -> None:
    # The only claim the string tests above cannot make: that re-registering
    # output 0 replaces the sampling script's raw clip rather than being
    # ignored. An 8-bit source settles it, because prepare() emits 10-bit and
    # the untouched clip would not.
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
            f"testsrc2=size=64x64:rate=24:duration={_SOURCE_FRAMES / 24}",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        check=True,
    )

    script = tmp_path / "vmaf.vpy"
    write_vpy_script(
        script,
        build_vmaf_vpy_script(
            ref_script=_sampling(IndexerType.FFMS2, source),
            dis_path=source,
            dis_cache=tmp_path / "dis.ffindex",
            indexer=IndexerType.FFMS2,
            info=_SDR,
        ),
    )

    outputs = _vspipe_outputs(script)

    assert len(outputs) == 2
    assert outputs[0]["Format Name"] == "YUV420P10"
    assert outputs[1]["Format Name"] == "YUV420P10"
    expected_frames = (_SOURCE_FRAMES // _INTERVAL_FRAMES) * _REGION_FRAMES
    assert outputs[0]["Frames"] == str(expected_frames)
