from __future__ import annotations

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
