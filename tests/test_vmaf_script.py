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
from videotuner.metric_colour import (
    ColourTarget,
    LinearisationMode,
    colour_preamble,
    vmaf_encode_source,
)
from videotuner.vmaf_script import build_vmaf_vpy_script, read_ssim2_scores

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
    _ = compile(_script(info), "vmaf.vpy", "exec")


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
    # An SDR clip bound for VMAF converts nothing, so the tell is the absence
    # of SSIMULACRA2's linear-light conversion rather than the presence of a
    # VMAF-specific one.
    sdr = _script(_SDR)
    assert "vs.RGBS" not in sdr
    assert "vszip" not in sdr

    # HDR still carries VMAF's own encode back to SDR, which SSIMULACRA2
    # never emits.
    assert "vs.YUV420P10" in _script(_PQ)


_SCORES = Path("scores") / "ssim2.txt"


def _combined(
    info: VideoInfo,
    indexer: IndexerType = IndexerType.FFMS2,
    scores: Path = _SCORES,
) -> str:
    return build_vmaf_vpy_script(
        ref_script=_sampling(indexer),
        dis_path=Path("dis.mkv"),
        dis_cache=Path("dis.ffindex"),
        indexer=indexer,
        info=info,
        ssim2_scores=scores,
    )


@pytest.mark.parametrize(
    "info", [_PQ, _HLG, _PQ_NO_MASTERING], ids=["pq", "hlg", "pq_no_mastering"]
)
def test_the_combined_script_is_valid_python(info: VideoInfo) -> None:
    _ = compile(_combined(info), "vmaf.vpy", "exec")


def test_the_combined_script_prepares_both_clips_for_ssim2() -> None:
    # SSIMULACRA2 scores linear light, so prepare() is its chain, not VMAF's.
    src = _combined(_PQ)
    assert colour_preamble(LinearisationMode.PQ, missing=[]) in src
    assert "ref = prepare(sampled)" in src
    assert "ssim2 = ref.vszip.SSIMULACRA2(dis)" in src


def test_vmaf_takes_the_same_frames_encoded_back() -> None:
    src = _combined(_PQ)
    assert vmaf_encode_source() in src
    assert "vref = encode_for_vmaf(ref)" in src
    assert "encode_for_vmaf(dis).set_output(1)" in src
    # VMAF's own chain is not emitted beside it: one prepare, one tonemap.
    vmaf_chain = colour_preamble(
        LinearisationMode.PQ, missing=[], target=ColourTarget.VMAF
    )
    assert vmaf_chain not in src
    assert src.count("core.tonemap.BT2390(") == 1


def test_the_score_callback_rides_on_output_zero() -> None:
    # Output 0 is what ffmpeg pulls frame by frame, so wrapping it is what
    # makes every SSIMULACRA2 frame get computed and written.
    src = _combined(_PQ)
    assert (
        "vref = core.std.ModifyFrame(vref, clips=[vref, ssim2], "
        + "selector=_record_ssim2)"
    ) in src
    assert "vref.set_output(0)" in src
    assert src.index("ModifyFrame(") < src.index("vref.set_output(0)")


def test_an_awkward_scores_path_still_gives_valid_python() -> None:
    # The scores file sits under the repository root, whose path can hold
    # an apostrophe, a space or non-ASCII characters.
    scores = Path("C:/it's a dir/wärme") / "ssim2.txt"
    src = _combined(_PQ, scores=scores)
    _ = compile(src, "vmaf.vpy", "exec")
    assert repr(str(scores)) in src


def test_the_combined_script_loads_through_the_selected_indexer() -> None:
    assert "core.ffms2.Source(" in _combined(_PQ, IndexerType.FFMS2)
    lsmash = _combined(_PQ, IndexerType.LSMASH)
    assert "core.lsmas.LWLibavSource(" in lsmash
    assert "core.ffms2.Source(" not in lsmash


_HLG_UNTAGGED = VideoInfo(fps=24.0, duration=1.0, color_trc="arib-std-b67")


def test_the_combined_script_writes_the_same_fallback_tags() -> None:
    # A partially tagged HDR input gets its missing tags from the same
    # colour_preamble call the separate SSIMULACRA2 pass makes: BT.2020 for
    # an HDR input, never BT.709.
    src = _combined(_HLG_UNTAGGED)
    assert "core.std.SetFrameProps(clip, _Primaries=9, _Matrix=9)" in src


def test_an_sdr_input_cannot_take_the_combined_pass() -> None:
    # An SDR clip goes to VMAF unconverted. Deriving its VMAF frames from
    # SSIMULACRA2's would change the VMAF score with no error.
    with pytest.raises(ValueError, match="HDR"):
        _ = _combined(_SDR)


def test_without_a_scores_file_the_script_scores_vmaf_alone() -> None:
    src = _script(_PQ)
    assert "vszip" not in src
    assert "ModifyFrame" not in src


def test_scores_are_read_in_frame_order(tmp_path: Path) -> None:
    # Frames are requested concurrently, so lines arrive out of order.
    path = tmp_path / "ssim2.txt"
    _ = path.write_text("2:70.5\n0:80.25\n1:75.0\n", encoding="utf-8")
    assert read_ssim2_scores(path) == [80.25, 75.0, 70.5]


def test_a_frame_written_twice_counts_once(tmp_path: Path) -> None:
    # A frame evicted from the cache and requested again is written again,
    # with the same score.
    path = tmp_path / "ssim2.txt"
    _ = path.write_text("0:80.25\n1:75.0\n1:75.0\n", encoding="utf-8")
    assert read_ssim2_scores(path) == [80.25, 75.0]


def test_a_score_survives_exactly(tmp_path: Path) -> None:
    value = 83.664199143915
    path = tmp_path / "ssim2.txt"
    _ = path.write_text(f"0:{value!r}\n", encoding="utf-8")
    assert read_ssim2_scores(path) == [value]


def test_a_missing_scores_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no SSIMULACRA2 scores"):
        _ = read_ssim2_scores(tmp_path / "ssim2.txt")


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
    #
    # PQ rather than SDR, because an SDR clip bound for VMAF is returned
    # unconverted and would leave nothing to tell the two apart. Tonemapping
    # is what makes the depth change observable here; it is the output
    # replacement being tested, not the colour.
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
            "-color_trc",
            "smpte2084",
            "-color_primaries",
            "bt2020",
            "-colorspace",
            "bt2020nc",
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
            # No mastering display, so the tone curve takes the assumed bounds
            # rather than reading properties this generated clip lacks.
            info=_PQ_NO_MASTERING,
        ),
    )

    outputs = _vspipe_outputs(script)

    assert len(outputs) == 2
    assert outputs[0]["Format Name"] == "YUV420P10"
    assert outputs[1]["Format Name"] == "YUV420P10"
    expected_frames = (_SOURCE_FRAMES // _INTERVAL_FRAMES) * _REGION_FRAMES
    assert outputs[0]["Frames"] == str(expected_frames)
