from __future__ import annotations

import os
import subprocess
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
    src = colour_preamble(LinearisationMode.PQ, retag=False)
    assert "core.tonemap.BT2390(" in src
    assert "core.tonemap.BT2407(" in src
    assert "core.tonemap.HLG(" not in src


def test_hlg_preamble_decodes_the_ootf_before_tonemapping() -> None:
    src = colour_preamble(LinearisationMode.HLG, retag=False)
    assert src.index("core.tonemap.HLG(") < src.index("core.tonemap.BT2390(")


def test_sdr_preamble_does_not_tonemap() -> None:
    src = colour_preamble(LinearisationMode.SDR, retag=False)
    assert "core.tonemap." not in src


def test_src_max_is_never_passed_to_the_tone_curve() -> None:
    # Supplying the bounds by hand defeats the plugin's contract check and
    # returns wrong colour on a mis-wired chain. The mastering display
    # properties must carry them instead.
    for mode in LinearisationMode:
        src = colour_preamble(mode, retag=False)
        assert "src_max" not in src
        assert "src_min" not in src


def test_retag_writes_the_fallback_properties() -> None:
    src = colour_preamble(LinearisationMode.SDR, retag=True)
    assert "SetFrameProps" in src
    assert "_Transfer=1" in src
    assert "_Primaries=1" in src
    assert "_Matrix=1" in src


def test_no_retag_leaves_the_properties_alone() -> None:
    src = colour_preamble(LinearisationMode.SDR, retag=False)
    assert "SetFrameProps" not in src


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
_CASES = {
    # mode: (clip construction, expected linear value)
    "sdr": (
        "clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, "
        "length=1, color=[128, 128, 128])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=1, "
        "_Primaries=1, _Range=0)",
        0.200013,
    ),
    "pq": (
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=16, "
        "_Primaries=9, _Range=0, MasteringDisplayMaxLuminance=1000.0, "
        "MasteringDisplayMinLuminance=0.0001)",
        0.504053,
    ),
    "hlg": (
        "clip = core.std.BlankClip(format=vs.RGB48, width=64, height=64, "
        "length=1, color=[32768, 32768, 32768])\n"
        "clip = core.std.SetFrameProps(clip, _Matrix=0, _Transfer=18, "
        "_Primaries=9, _Range=0)",
        0.263935,
    ),
}


def _run_chain(tmp_path: Path, mode: LinearisationMode) -> dict[str, Any]:
    """Execute the emitted chain under the portable VapourSynth."""
    construction, _ = _CASES[mode.value]
    script = tmp_path / "chain.vpy"
    _ = script.write_text(
        "import vapoursynth as vs\n"
        "core = vs.core\n\n"
        f"{colour_preamble(mode, retag=False)}\n"
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
@pytest.mark.parametrize("mode", list(LinearisationMode))
def test_chain_emits_linear_rgbs_in_bt709_primaries(
    tmp_path: Path, mode: LinearisationMode
) -> None:
    got = _run_chain(tmp_path, mode)

    assert got["format"] == "RGBS"
    assert got["_Matrix"] == 0  # RGB
    assert got["_Transfer"] == 8  # linear
    assert got["_Primaries"] == 1  # BT.709
    assert got["_Range"] == 1  # full


@requires_vapoursynth
@pytest.mark.parametrize("mode", list(LinearisationMode))
def test_chain_produces_the_measured_linear_value(
    tmp_path: Path, mode: LinearisationMode
) -> None:
    _, expected = _CASES[mode.value]
    got = _run_chain(tmp_path, mode)

    assert len(got["values"]) == 3
    for channel in got["values"]:
        assert channel == pytest.approx(expected, abs=1e-4)
        assert 0.0 <= channel <= 1.0


@requires_vapoursynth
def test_tonemapping_consumes_the_mastering_display_metadata(tmp_path: Path) -> None:
    # BT2390 reads src_min/src_max from these and removes them. If they survive,
    # the tone curve did not run.
    got = _run_chain(tmp_path, LinearisationMode.PQ)

    assert got["absent_MasteringDisplayMaxLuminance"] is True
    assert got["absent_MasteringDisplayMinLuminance"] is True
