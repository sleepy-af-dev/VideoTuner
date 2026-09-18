from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from videotuner.encoding_utils import (
    IndexerType,
    SampledSource,
    UsableRange,
    VapourSynthEnv,
    build_sampling_vpy_script,
    indexer_spec,
)
from videotuner.media import VideoInfo
from videotuner.metric_colour import (
    _FALLBACK_VALUES,
    LinearisationMode,
    MetricTarget,
    colour_assumption_notes,
    colour_preamble,
    linearisation_mode,
    missing_colour_tags,
    missing_mastering_display,
)
from videotuner.ssimulacra2_assessment import _build_ssim2_script, _run_vszip_assessment


@pytest.mark.parametrize(
    ("trc", "expected"),
    [
        ("smpte2084", LinearisationMode.PQ),
        ("pq", LinearisationMode.PQ),
        ("SMPTE2084", LinearisationMode.PQ),
        ("arib-std-b67", LinearisationMode.HLG),
        ("hlg", LinearisationMode.HLG),
        ("bt709", LinearisationMode.SDR),
        ("smpte170m", LinearisationMode.SDR),
        (None, LinearisationMode.SDR),
        ("", LinearisationMode.SDR),
    ],
)
def test_transfer_selects_the_chain_variant(
    trc: str | None, expected: LinearisationMode
) -> None:
    assert linearisation_mode(trc) is expected


def test_untagged_source_reports_every_missing_tag() -> None:
    info = VideoInfo(fps=24.0, duration=1.0)
    assert missing_colour_tags(info) == ["transfer", "primaries", "matrix"]


def test_fully_tagged_source_reports_nothing_missing() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="bt709",
        color_primaries="bt709",
        color_space="bt709",
    )
    assert missing_colour_tags(info) == []


def test_partially_tagged_source_names_only_what_is_absent() -> None:
    info = VideoInfo(fps=24.0, duration=1.0, color_trc="bt709")
    assert missing_colour_tags(info) == ["primaries", "matrix"]


def test_pq_preamble_tonemaps_and_maps_gamut() -> None:
    src = colour_preamble(LinearisationMode.PQ, missing=[])
    assert "core.tonemap.BT2390(" in src
    assert "core.tonemap.BT2407(" in src
    assert "core.tonemap.HLG(" not in src


def test_hlg_preamble_decodes_the_ootf_before_tonemapping() -> None:
    src = colour_preamble(LinearisationMode.HLG, missing=[])
    assert src.index("core.tonemap.HLG(") < src.index("core.tonemap.BT2390(")


def test_sdr_preamble_does_not_tonemap() -> None:
    src = colour_preamble(LinearisationMode.SDR, missing=[])
    assert "core.tonemap." not in src


def test_bounds_are_not_passed_while_the_properties_carry_them() -> None:
    # Supplying the bounds by hand defeats the plugin's contract check and
    # returns wrong colour on a mis-wired chain. While the mastering display
    # properties are there to read, they carry them instead.
    for mode in LinearisationMode:
        src = colour_preamble(mode, missing=[])
        assert "src_max" not in src
        assert "src_min" not in src


def test_pq_assumes_a_peak_when_no_mastering_display_will_be_there() -> None:
    # The literal rather than the constant on purpose: changing the assumed
    # peak should break a test and be argued for, not pass silently.
    src = colour_preamble(LinearisationMode.PQ, missing=[], mastering_missing=True)
    assert "src_max=1000" in src
    assert "src_min=0" in src


@pytest.mark.parametrize("mode", [LinearisationMode.HLG, LinearisationMode.SDR])
def test_only_pq_assumes_a_peak(mode: LinearisationMode) -> None:
    # tonemap.HLG already defaults its own peak to 1000 and writes the
    # properties BT2390 then reads. SDR never tonemaps at all.
    src = colour_preamble(mode, missing=[], mastering_missing=True)
    assert "src_max" not in src
    assert "src_min" not in src


def test_untagged_source_has_no_mastering_display() -> None:
    info = VideoInfo(fps=24.0, duration=1.0)
    assert missing_mastering_display(info) is True


def test_a_declared_mastering_display_is_not_missing() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        mastering_display_color_primaries="BT.2020",
        mastering_display_luminance="min: 0.0001 cd/m2, max: 1000 cd/m2",
    )
    assert missing_mastering_display(info) is False


def test_luminance_without_primaries_is_still_missing() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        mastering_display_luminance="min: 0.0001 cd/m2, max: 1000 cd/m2",
    )
    assert missing_mastering_display(info) is True


def test_fully_missing_sdr_writes_all_three_bt709_fallbacks() -> None:
    src = colour_preamble(
        LinearisationMode.SDR, missing=["transfer", "primaries", "matrix"]
    )
    assert "SetFrameProps" in src
    assert "_Transfer=1" in src
    assert "_Primaries=1" in src
    assert "_Matrix=1" in src


@pytest.mark.parametrize("mode", list(LinearisationMode))
def test_fully_tagged_input_leaves_the_properties_alone(
    mode: LinearisationMode,
) -> None:
    assert "SetFrameProps" not in colour_preamble(mode, missing=[])


def test_pq_with_only_primaries_missing_does_not_touch_transfer() -> None:
    # The regression this guards: a partially-tagged PQ input had its
    # correct transfer overwritten with BT.709 just because primaries was
    # also missing, silently linearising real PQ samples through the wrong
    # curve.
    src = colour_preamble(LinearisationMode.PQ, missing=["primaries"])
    assert "_Primaries=9" in src
    assert "_Transfer=" not in src


def test_hlg_with_only_matrix_missing_touches_only_matrix() -> None:
    src = colour_preamble(LinearisationMode.HLG, missing=["matrix"])
    assert "_Matrix=9" in src
    assert "_Transfer=" not in src
    assert "_Primaries=" not in src


REPO_ROOT = Path(__file__).resolve().parents[1]
VS_DIR = REPO_ROOT / "vapoursynth-portable"
VS_PYTHON = VS_DIR / "python.exe"
VS_PLUGINS = VS_DIR / "vs-plugins"

requires_vapoursynth = pytest.mark.skipif(
    not VS_PYTHON.exists() or not (VS_PLUGINS / "vs-tonemap").exists(),
    reason="vapoursynth-portable is not provisioned; run tools/update_vs.py",
)


def _ffmpeg_has_libx265() -> bool:
    """Decide once whether the ffmpeg on PATH can encode libx265."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return "libx265" in proc.stdout


requires_libx265 = pytest.mark.skipif(
    not _ffmpeg_has_libx265(),
    reason="ffmpeg on PATH has no libx265 encoder",
)

# Measured against vs-tonemap 0.2.0. A plugin update that moves these is a
# result worth seeing rather than a test to relax: the numbers are the operating
# point every SSIMULACRA2 score is computed at.
#
# _Range=0 is limited range, _Range=1 is full.
_CASES: dict[str, tuple[LinearisationMode, str, tuple[float, float, float], bool]] = {
    # case: (mode, clip construction, expected linear value, mastering_missing)
    "sdr": (
        LinearisationMode.SDR,
        "clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, "
        "length=1, color=[128, 128, 128])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=1, "
        "_Primaries=1, _Range=0)",
        (0.200013, 0.200013, 0.200013),
        False,
    ),
    "pq": (
        LinearisationMode.PQ,
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=16, "
        "_Primaries=9, _Range=0, MasteringDisplayMaxLuminance=1000.0, "
        "MasteringDisplayMinLuminance=0.0001)",
        (0.504053, 0.504053, 0.504053),
        False,
    ),
    "hlg": (
        LinearisationMode.HLG,
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=18, "
        "_Primaries=9, _Range=0)",
        (0.263935, 0.263935, 0.263935),
        False,
    ),
    # Neutral grey is white-point invariant, so the cases above exercise the
    # primaries conversion not at all, and on grey the failure the HLG chain is
    # shaped to avoid separates correct from incorrect by 1.28e-4 against a
    # 1e-4 tolerance. Saturated blue is where that failure has signal:
    # linearising each channel through zimg's std-b67, instead of letting
    # tonemap.HLG apply one OOTF scalar, measures (0.203753, 0.246870, 1.0)
    # here. That is 0.14 away on red and green, and 1.76x the BT.709 luminance,
    # which is the "76% too bright" metric_colour.py's HLG comment names.
    #
    # Tagged full range so peak blue is unambiguous rather than sitting past a
    # limited-range white point.
    "hlg-saturated-blue": (
        LinearisationMode.HLG,
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[0, 0, 65535])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=18, "
        "_Primaries=9, _Range=1)",
        (0.062000, 0.112774, 1.000000),
        False,
    ),
    # The same PQ grey with its mastering display stripped. BT2390 raises
    # without a peak, so the chain hands it the assumed one. 0.504143 rather
    # than the tagged case's 0.504053 because that case declares a 0.0001
    # cd/m2 black and the assumption uses 0. The gap is 9e-5, just inside the
    # 1e-4 tolerance, so the two cases carry their own measured values rather
    # than sharing one.
    "pq-no-mastering-display": (
        LinearisationMode.PQ,
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=16, "
        "_Primaries=9, _Range=0)",
        (0.504143, 0.504143, 0.504143),
        True,
    ),
}


def test_every_conversion_has_an_executing_case() -> None:
    assert {mode for mode, _, _, _ in _CASES.values()} == set(LinearisationMode)


def _run_chain(tmp_path: Path, case: str) -> dict[str, Any]:
    """Execute the emitted chain under the portable VapourSynth."""
    mode, construction, _, mastering_missing = _CASES[case]
    script = tmp_path / "chain.vpy"
    _ = script.write_text(
        "import vapoursynth as vs\n"
        "core = vs.core\n\n"
        f"{colour_preamble(mode, missing=[], mastering_missing=mastering_missing)}\n"
        f"{construction}\n"
        "out = prepare(clip)\n"
        "f = out.get_frame(0)\n"
        'print("FORMAT:" + out.format.name)\n'
        'for k in ("_Matrix", "_Transfer", "_Primaries", "_Range"):\n'
        '    print("PROP:" + k + "=" + str(int(f.props[k])))\n'
        'for k in ("MasteringDisplayMaxLuminance", "MasteringDisplayMinLuminance"):\n'
        '    print("ABSENT:" + k + "=" + str(k not in f.props))\n'
        "for p in range(3):\n"
        "    s = core.std.PlaneStats(out, plane=p).get_frame(0).props\n"
        '    print("VALUE:%.6f" % float(s["PlaneStatsAverage"]))\n',
        encoding="utf-8",
    )

    env = {**os.environ, "VAPOURSYNTH_EXTRA_PLUGIN_PATH": str(VS_PLUGINS)}
    proc = subprocess.run(
        [str(VS_PYTHON), str(script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"chain failed:\n{proc.stderr}"

    out: dict[str, Any] = {"values": []}
    for line in proc.stdout.splitlines():
        if line.startswith("FORMAT:"):
            out["format"] = line[7:]
        elif line.startswith("PROP:"):
            key, _, value = line[5:].partition("=")
            out[key] = int(value)
        elif line.startswith("ABSENT:"):
            key, _, value = line[7:].partition("=")
            out[f"absent_{key}"] = value == "True"
        elif line.startswith("VALUE:"):
            out["values"].append(float(line[6:]))
    return out


@requires_vapoursynth
@pytest.mark.parametrize("case", list(_CASES))
def test_chain_emits_linear_rgbs_in_bt709_primaries(tmp_path: Path, case: str) -> None:
    got = _run_chain(tmp_path, case)

    assert got["format"] == "RGBS"
    assert got["_Matrix"] == 0  # RGB
    assert got["_Transfer"] == 8  # linear
    assert got["_Primaries"] == 1  # BT.709
    assert got["_Range"] == 1  # full


@requires_vapoursynth
@pytest.mark.parametrize("case", list(_CASES))
def test_chain_produces_the_measured_linear_value(tmp_path: Path, case: str) -> None:
    _, _, expected, _ = _CASES[case]
    got = _run_chain(tmp_path, case)

    assert got["values"] == pytest.approx(expected, abs=1e-4)
    for channel in got["values"]:
        assert 0.0 <= channel <= 1.0


@requires_vapoursynth
def test_tonemapping_consumes_the_mastering_display_metadata(tmp_path: Path) -> None:
    # BT2390 reads src_min/src_max from these and removes them. If they survive,
    # the tone curve did not run.
    got = _run_chain(tmp_path, "pq")

    assert got["absent_MasteringDisplayMaxLuminance"] is True
    assert got["absent_MasteringDisplayMinLuminance"] is True


@requires_vapoursynth
def test_pq_without_a_mastering_display_fails_without_the_assumption(
    tmp_path: Path,
) -> None:
    # The gap this closes, at the point where it is still a clean error.
    # Wrapped in vszip.SSIMULACRA2 the same failure takes the whole process
    # down instead, which Task 3 covers. Same clip, same chain, assumption
    # withheld.
    mode, construction, _, _ = _CASES["pq-no-mastering-display"]
    script = tmp_path / "chain.vpy"
    _ = script.write_text(
        "import vapoursynth as vs\n"
        "core = vs.core\n\n"
        f"{colour_preamble(mode, missing=[])}\n"
        f"{construction}\n"
        "prepare(clip).get_frame(0)\n",
        encoding="utf-8",
    )

    env = {**os.environ, "VAPOURSYNTH_EXTRA_PLUGIN_PATH": str(VS_PLUGINS)}
    proc = subprocess.run(
        [str(VS_PYTHON), str(script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert proc.returncode != 0
    assert "MasteringDisplayMaxLuminance" in proc.stderr


_DEFAULT_SOURCE = Path("source.mkv")


def _sampling(
    indexer: IndexerType, source: Path = _DEFAULT_SOURCE, frames: int = 24
) -> str:
    """Build the reference half the way the pipeline does, by composing it."""
    return build_sampling_vpy_script(
        sources=[
            SampledSource(
                path=source,
                cache_file=source.with_suffix(indexer_spec(indexer).index_suffix),
                usable_range=UsableRange(start=0, end=frames, frame_count=frames),
            )
        ],
        # SelectEvery refuses a cycle of one, so the smallest selection that
        # keeps every frame is two out of every two.
        interval_frames=2,
        region_frames=2,
        fps=24.0,
        indexer=indexer,
    )


def _script(mode: LinearisationMode, *, missing: Sequence[str] = ()) -> str:
    return _build_ssim2_script(
        ref_script=_sampling(IndexerType.LSMASH),
        dis_path=Path("dis.mkv"),
        dis_cache=Path("dis.lwi"),
        preamble=colour_preamble(mode, missing=missing),
        indexer=IndexerType.LSMASH,
    )


@pytest.mark.parametrize("mode", list(LinearisationMode))
def test_the_ssim2_script_is_valid_python(mode: LinearisationMode) -> None:
    # A formatted string with the sampling script pasted into it, so a syntax
    # error in either half would pass every other test here and fail only
    # once VapourSynth ran it.
    compile(_script(mode), "ssim2.vpy", "exec")


def test_both_clips_go_through_the_chain() -> None:
    src = _script(LinearisationMode.PQ)
    assert "ref = prepare(ref)" in src
    assert "dis = prepare(dis)" in src


def test_the_chain_runs_before_the_metric() -> None:
    src = _script(LinearisationMode.PQ)
    assert src.index("dis = prepare(dis)") < src.index("vszip.SSIMULACRA2(")


def test_the_metric_call_is_unchanged() -> None:
    # vszip takes no colour arguments; correctness comes from what it is given.
    src = _script(LinearisationMode.SDR)
    assert "ref.vszip.SSIMULACRA2(dis)" in src


def test_sdr_scripts_carry_no_tonemapping() -> None:
    assert "core.tonemap." not in _script(LinearisationMode.SDR)


def test_frames_are_requested_concurrently() -> None:
    # A serial get_frame(i) loop forbids frame-level parallelism, which costs
    # about 3x on the tonemapped path. clip.frames() prefetches across the
    # core's worker threads and yields in order, so SCORE: lines stay ordered.
    src = _script(LinearisationMode.PQ)
    assert ".frames(" in src
    assert "get_frame(" not in src


def test_the_frame_loop_bounds_what_it_holds() -> None:
    # Uncapped, prefetch follows the core's thread count and every in-flight
    # frame is an RGBS clip at source resolution, so peak memory scales with
    # core count: measured at 19.2 GiB on 32 threads against 13.3 GiB capped.
    src = _script(LinearisationMode.PQ)
    assert "result.frames(prefetch=8, close=True)" in src


def test_a_tonemapped_script_guards_on_the_plugin() -> None:
    # Missing, the plugin raises AttributeError, which is swallowed upstream:
    # the SSIMULACRA2 column comes back empty with nothing said on the
    # terminal, and only HDR is affected, so it looks like it works.
    src = _script(LinearisationMode.PQ)
    assert "hasattr(core, 'tonemap')" in src
    assert "vs-tonemap" in src
    assert "tools/update_vs.py" in src


def test_an_sdr_script_does_not_demand_the_tonemap_plugin() -> None:
    # SDR never calls it, so requiring it would fail a run that works.
    assert "hasattr(core, 'tonemap')" not in _script(LinearisationMode.SDR)


def test_ssim2_script_loads_through_the_selected_indexer() -> None:
    src = _build_ssim2_script(
        ref_script=_sampling(IndexerType.FFMS2),
        dis_path=Path("dis.mkv"),
        dis_cache=Path("dis.ffindex"),
        preamble=colour_preamble(LinearisationMode.SDR, missing=[]),
        indexer=IndexerType.FFMS2,
    )
    assert "core.ffms2.Source(" in src
    assert "core.lsmas.LWLibavSource(" not in src


def test_the_indexer_hint_only_answers_a_failed_load() -> None:
    # The likeliest HDR failure is a PQ input with no mastering display
    # metadata, where the tone curve raises for want of a peak luminance.
    # Switching indexers cannot help with that, or with anything else the
    # colour chain or the metric raises, so the hint sits with the loads and
    # the handler around the rest of the script does not repeat it.
    src = _script(LinearisationMode.PQ)
    assert src.count("HINT:") == 1
    assert src.index("dis = core.") < src.index("HINT:") < src.index("ref = prepare(")


def test_ssim2_script_can_still_load_through_lsmash() -> None:
    src = _build_ssim2_script(
        ref_script=_sampling(IndexerType.LSMASH),
        dis_path=Path("dis.mkv"),
        dis_cache=Path("dis.lwi"),
        preamble=colour_preamble(LinearisationMode.SDR, missing=[]),
        indexer=IndexerType.LSMASH,
    )
    assert "core.lsmas.LWLibavSource(" in src
    assert "core.ffms2.Source(" not in src


def _write_pq_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a lossless PQ reference and a CRF 32 encode of the same source.

    Both are tagged PQ and neither declares a mastering display, which is
    exactly the case the assumption exists for. ffmpeg's own test pattern
    gives the metric real structure to score, so the result is not the
    degenerate 100 a flat colour field produces.
    """
    made: list[Path] = []
    for rate, name in (("lossless=1", "ref.mkv"), ("crf=32", "dis.mkv")):
        out = tmp_path / name
        _ = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=256x256:rate=24:duration=1",
                "-pix_fmt",
                "yuv420p10le",
                "-c:v",
                "libx265",
                "-x265-params",
                f"{rate}:colorprim=bt2020:transfer=smpte2084"
                ":colormatrix=bt2020nc:repeat-headers=1",
                str(out),
            ],
            capture_output=True,
            check=True,
        )
        made.append(out)
    return made[0], made[1]


@requires_vapoursynth
@requires_libx265
def test_a_pq_input_with_no_mastering_display_is_still_scored(
    tmp_path: Path,
) -> None:
    # Before the assumption this took the VapourSynth process down: BT2390
    # raises for want of a peak, and vszip does not survive an error from the
    # node it is scoring.
    ref, dis = _write_pq_pair(tmp_path)
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
    )

    result = _run_vszip_assessment(
        ref_script=_sampling(IndexerType.FFMS2, ref),
        dis_path=dis,
        vs_env=VapourSynthEnv.from_cwd(REPO_ROOT),
        temp_dir=tmp_path,
        info=info,
        indexer=IndexerType.FFMS2,
    )

    assert result.count == 24


def test_a_fully_declared_input_assumes_nothing() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        mastering_display_color_primaries="BT.2020",
        mastering_display_luminance="min: 0.0001 cd/m2, max: 1000 cd/m2",
    )
    assert colour_assumption_notes(info) == []


def test_an_untagged_sdr_input_reports_the_bt709_assumption() -> None:
    notes = colour_assumption_notes(VideoInfo(fps=24.0, duration=1.0))
    assert len(notes) == 1
    assert "transfer, primaries, matrix" in notes[0]
    assert "BT.709" in notes[0]


def test_an_untagged_pq_input_reports_bt2020_not_bt709() -> None:
    # A PQ input that omits its primaries is overwhelmingly BT.2020. The
    # note has to name what was assumed, not only what was missing.
    info = VideoInfo(fps=24.0, duration=1.0, color_trc="smpte2084")
    notes = colour_assumption_notes(info)
    assert any("BT.2020" in note for note in notes)
    assert not any("BT.709" in note for note in notes)


@pytest.mark.parametrize(
    "trc",
    [
        None,  # untagged SDR: assumes all three tags
        "smpte2084",  # untagged PQ: assumes tags and a mastering peak
        "arib-std-b67",  # untagged HLG: assumes tags, never a peak
    ],
)
def test_every_assumption_note_names_both_metrics(trc: str | None) -> None:
    # Both metrics read through one colour chain, so an assumed tag or an
    # assumed peak shapes a VMAF score exactly as it shapes a SSIMULACRA2
    # one. A note naming a single metric reads as though the other were
    # exempt. That was a real defect: the warning was once gated on
    # SSIMULACRA2 alone and worded for it alone, while VMAF ran the same
    # chain and said nothing.
    #
    # Asserting the pairing rather than the exact sentence, so rewording
    # stays free while dropping a metric does not.
    notes = colour_assumption_notes(VideoInfo(fps=24.0, duration=1.0, color_trc=trc))

    assert notes, "this input should assume something, or it pins nothing"
    for note in notes:
        assert ("VMAF" in note) == ("SSIMULACRA2" in note), (
            f"note names one metric but not the other: {note!r}"
        )


# The note names a colour space in words. _FALLBACK_VALUES holds the numeric
# code the chain actually writes into the frame properties. They are one
# decision expressed twice, so a change to the table that did not reach the
# note would tell a user the wrong thing about their scores, and the tests
# above would not notice because they assert the words. CICP primaries: 1 is
# BT.709, 9 is BT.2020.
_PRIMARIES_NAMES = {1: "BT.709", 9: "BT.2020"}


@pytest.mark.parametrize(
    ("trc", "mode"),
    [
        (None, LinearisationMode.SDR),
        ("smpte2084", LinearisationMode.PQ),
        ("arib-std-b67", LinearisationMode.HLG),
    ],
)
def test_the_note_names_the_primaries_the_chain_writes(
    trc: str | None, mode: LinearisationMode
) -> None:
    notes = colour_assumption_notes(VideoInfo(fps=24.0, duration=1.0, color_trc=trc))

    # The tag note comes first: colour_assumption_notes returns one message
    # per assumption, in the order the chain makes them, and the tags are
    # settled before the tone curve needs a peak.
    tag_note = notes[0]
    expected = _PRIMARIES_NAMES[_FALLBACK_VALUES[mode]["primaries"]]

    assert expected in tag_note
    for other in set(_PRIMARIES_NAMES.values()) - {expected}:
        assert other not in tag_note


def test_a_pq_input_with_no_mastering_display_reports_the_peak() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
    )
    notes = colour_assumption_notes(info)
    assert len(notes) == 1
    assert "no mastering display" in notes[0]
    assert "1000 cd/m2" in notes[0]


def test_an_hlg_input_reports_no_peak_assumption() -> None:
    # tonemap.HLG defaults its own peak and writes the properties BT2390
    # then reads, so there is nothing for the chain to assume.
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="arib-std-b67",
        color_primaries="bt2020",
        color_space="bt2020nc",
    )
    assert colour_assumption_notes(info) == []


def test_an_sdr_input_reports_no_peak_assumption() -> None:
    info = VideoInfo(
        fps=24.0,
        duration=1.0,
        color_trc="bt709",
        color_primaries="bt709",
        color_space="bt709",
    )
    assert colour_assumption_notes(info) == []


def test_the_ssim2_target_ends_in_linear_rgbs() -> None:
    src = colour_preamble(LinearisationMode.SDR, missing=[], target=MetricTarget.SSIM2)
    assert "vs.RGBS" in src
    assert "vs.YUV420P10" not in src


def test_the_vmaf_target_ends_in_limited_range_yuv() -> None:
    # PQ rather than SDR: an SDR clip bound for VMAF is already in the form
    # VMAF wants and is returned unconverted, so this tail is only reached
    # once a tone curve has run and the frames have to be encoded back.
    src = colour_preamble(LinearisationMode.PQ, missing=[], target=MetricTarget.VMAF)
    assert "vs.YUV420P10" in src
    assert 'matrix_s="709"' in src
    assert 'range_s="limited"' in src


def test_the_default_target_is_ssim2() -> None:
    # Every existing caller passes no target, and must keep the chain it had.
    assert colour_preamble(LinearisationMode.PQ, missing=[]) == colour_preamble(
        LinearisationMode.PQ, missing=[], target=MetricTarget.SSIM2
    )


@pytest.mark.parametrize("mode", list(LinearisationMode))
def test_both_targets_share_one_tonemap_call(mode: LinearisationMode) -> None:
    # The point of one module: the front cannot differ between metrics.
    ssim2 = colour_preamble(mode, missing=[], target=MetricTarget.SSIM2)
    vmaf = colour_preamble(mode, missing=[], target=MetricTarget.VMAF)
    for call in ("core.tonemap.BT2390(", "core.tonemap.BT2407(", "core.tonemap.HLG("):
        assert (call in ssim2) == (call in vmaf)


# Measured against vs-tonemap 0.2.0 on 2026-09-18. _Range=0 is limited.
# SDR is absent on purpose: it tonemaps nothing, so its VMAF chain converts
# nothing either and is covered by the passthrough tests below instead.
_VMAF_CASES: dict[str, tuple[str, tuple[float, float, float]]] = {
    "pq": ("pq", (0.705767, 0.500489, 0.500489)),
}


@requires_vapoursynth
@pytest.mark.parametrize("case", list(_VMAF_CASES))
def test_the_vmaf_chain_emits_limited_range_bt709_yuv(
    tmp_path: Path, case: str
) -> None:
    source_case, expected = _VMAF_CASES[case]
    mode, construction, _, mastering_missing = _CASES[source_case]
    script = tmp_path / "chain.vpy"
    preamble = colour_preamble(
        mode, missing=[], mastering_missing=mastering_missing, target=MetricTarget.VMAF
    )
    _ = script.write_text(
        "import vapoursynth as vs\n"
        "core = vs.core\n\n"
        f"{preamble}\n"
        f"{construction}\n"
        "out = prepare(clip)\n"
        "f = out.get_frame(0)\n"
        'print("FORMAT:" + out.format.name)\n'
        'for k in ("_Matrix", "_Transfer", "_Range"):\n'
        '    print("PROP:" + k + "=" + str(int(f.props[k])))\n'
        "for p in range(3):\n"
        "    s = core.std.PlaneStats(out, plane=p).get_frame(0).props\n"
        '    print("VALUE:%.6f" % float(s["PlaneStatsAverage"]))\n',
        encoding="utf-8",
    )

    env = {**os.environ, "VAPOURSYNTH_EXTRA_PLUGIN_PATH": str(VS_PLUGINS)}
    proc = subprocess.run(
        [str(VS_PYTHON), str(script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"chain failed:\n{proc.stderr}"

    out: dict[str, int | str] = {}
    values: list[float] = []
    for line in proc.stdout.splitlines():
        if line.startswith("FORMAT:"):
            out["format"] = line[7:]
        elif line.startswith("PROP:"):
            key, _, value = line[5:].partition("=")
            out[key] = int(value)
        elif line.startswith("VALUE:"):
            values.append(float(line[6:]))

    assert out["format"] == "YUV420P10"
    assert out["_Matrix"] == 1  # BT.709
    assert out["_Transfer"] == 1  # BT.709
    assert out["_Range"] == 0  # limited
    assert values == pytest.approx(expected, abs=1e-4)


def test_an_sdr_clip_bound_for_vmaf_is_not_converted_at_all() -> None:
    # VMAF reads code values, and its models were fitted on SDR video in its
    # native encoded form, so the frames it wants are the ones already on
    # disk. Routing them through linear RGB and back resamples chroma
    # 420-444-420, applies a 709 EOTF/OETF pair, converts range twice and
    # requantises — lossy, pointless when nothing tonemaps, and it smooths
    # both clips alike so every SDR score comes out flattered.
    src = colour_preamble(LinearisationMode.SDR, missing=[], target=MetricTarget.VMAF)
    assert "core.resize." not in src
    assert "core.tonemap." not in src
    assert "vs.RGBS" not in src
    assert "return clip" in src


def test_an_sdr_clip_bound_for_ssimulacra2_is_still_converted() -> None:
    # SSIMULACRA2 scores in linear light by definition, so its SDR chain is
    # not the one being removed.
    src = colour_preamble(LinearisationMode.SDR, missing=[], target=MetricTarget.SSIM2)
    assert "vs.RGBS" in src
    assert 'transfer_s="linear"' in src


@pytest.mark.parametrize("mode", [LinearisationMode.PQ, LinearisationMode.HLG])
def test_an_hdr_clip_bound_for_vmaf_still_tonemaps(mode: LinearisationMode) -> None:
    # Only SDR skips the conversion. HDR genuinely needs it: VMAF is not
    # defined on HDR, so the tonemap and the encode back to SDR both stay.
    src = colour_preamble(mode, missing=[], target=MetricTarget.VMAF)
    assert "core.tonemap.BT2390(" in src
    assert "vs.YUV420P10" in src


def test_an_untagged_sdr_clip_bound_for_vmaf_still_gets_its_tags() -> None:
    # The passthrough skips the conversion, not the tagging: vspipe -c mkv
    # writes these properties into the stream it hands ffmpeg.
    src = colour_preamble(
        LinearisationMode.SDR,
        missing=["transfer", "primaries", "matrix"],
        target=MetricTarget.VMAF,
    )
    assert "SetFrameProps" in src
    assert "_Transfer=1" in src
    assert "return clip" in src
