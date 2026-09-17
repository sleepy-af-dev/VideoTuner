from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from videotuner.media import VideoInfo
from videotuner.ssim2_colour import (
    LinearisationMode,
    colour_preamble,
    linearisation_mode,
    missing_colour_tags,
)
from videotuner.ssimulacra2_assessment import _build_ssim2_script


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


def test_src_max_is_never_passed_to_the_tone_curve() -> None:
    # Supplying the bounds by hand defeats the plugin's contract check and
    # returns wrong colour on a mis-wired chain. The mastering display
    # properties must carry them instead.
    for mode in LinearisationMode:
        src = colour_preamble(mode, missing=[])
        assert "src_max" not in src
        assert "src_min" not in src


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

# Measured against vs-tonemap 0.2.0. A plugin update that moves these is a
# result worth seeing rather than a test to relax: the numbers are the operating
# point every SSIMULACRA2 score is computed at.
#
# _Range=0 is limited range, _Range=1 is full.
_CASES: dict[str, tuple[LinearisationMode, str, tuple[float, float, float]]] = {
    # case: (mode, clip construction, expected linear value per channel)
    "sdr": (
        LinearisationMode.SDR,
        "clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, "
        "length=1, color=[128, 128, 128])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=1, "
        "_Primaries=1, _Range=0)",
        (0.200013, 0.200013, 0.200013),
    ),
    "pq": (
        LinearisationMode.PQ,
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=16, "
        "_Primaries=9, _Range=0, MasteringDisplayMaxLuminance=1000.0, "
        "MasteringDisplayMinLuminance=0.0001)",
        (0.504053, 0.504053, 0.504053),
    ),
    "hlg": (
        LinearisationMode.HLG,
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=18, "
        "_Primaries=9, _Range=0)",
        (0.263935, 0.263935, 0.263935),
    ),
    # Neutral grey is white-point invariant, so the cases above exercise the
    # primaries conversion not at all, and on grey the failure the HLG chain is
    # shaped to avoid separates correct from incorrect by 1.28e-4 against a
    # 1e-4 tolerance. Saturated blue is where that failure has signal:
    # linearising each channel through zimg's std-b67, instead of letting
    # tonemap.HLG apply one OOTF scalar, measures (0.203753, 0.246870, 1.0)
    # here. That is 0.14 away on red and green, and 1.76x the BT.709 luminance,
    # which is the "76% too bright" ssim2_colour.py's HLG comment names.
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
    ),
}


def test_every_conversion_has_an_executing_case() -> None:
    assert {mode for mode, _, _ in _CASES.values()} == set(LinearisationMode)


def _run_chain(tmp_path: Path, case: str) -> dict[str, Any]:
    """Execute the emitted chain under the portable VapourSynth."""
    mode, construction, _ = _CASES[case]
    script = tmp_path / "chain.vpy"
    _ = script.write_text(
        "import vapoursynth as vs\n"
        "core = vs.core\n\n"
        f"{colour_preamble(mode, missing=[])}\n"
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
    _, _, expected = _CASES[case]
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


def _script(mode: LinearisationMode, *, missing: Sequence[str] = ()) -> str:
    return _build_ssim2_script(
        ref_path=Path("ref.mkv"),
        dis_path=Path("dis.mkv"),
        ref_cache=Path("ref.lwi"),
        dis_cache=Path("dis.lwi"),
        preamble=colour_preamble(mode, missing=missing),
    )


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
