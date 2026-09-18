"""Colour handling for SSIMULACRA2 assessment.

SSIMULACRA2 is an SDR metric: its weights were fitted on SDR still-image
datasets and it has no concept of absolute luminance. The reference
implementation scores in linear light, colour-managed from whatever encoding
the input declares.

vszip does neither. It overwrites ``_Transfer`` with sRGB before linearising
and never reads ``_Primaries``, so an HDR input is linearised through the wrong
curve and scored in a domain the metric was never fitted for.

This module emits the VapourSynth source that hands vszip a clip already in
linear RGBS. Both of vszip's conversion paths early-return on such a clip -
RGBS skips its ``toRGBS``, and a linear transfer skips its sRGB step - so the
pre-converted frames reach the metric untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

from .constants import SSIM2_ASSUMED_MASTERING_PEAK, SSIM2_NOMINAL_LUMINANCE
from .encoding_utils import is_hdr_video, is_pq_video
from .utils import master_display_arg

if TYPE_CHECKING:
    from .media import VideoInfo


#: Label for the operator that takes an HDR input to SDR before scoring. VMAF
#: tonemaps with a different one, so the settings header names both: on a
#: machine without a usable GPU they disagree, and an HDR score is not
#: interpretable without knowing which produced it.
SSIM2_TONEMAP_BACKEND = "vs-tonemap (BT.2390 + BT.2407)"


class LinearisationMode(StrEnum):
    """Which conversion an input's transfer requires."""

    PQ = "pq"
    HLG = "hlg"
    SDR = "sdr"


def linearisation_mode(color_trc: str | None) -> LinearisationMode:
    """Pick the conversion for an input's declared transfer.

    HDR is defined as the union of the PQ and HLG transfer sets, so there is no
    third HDR case that can appear here silently.

    Args:
        color_trc: Transfer characteristic from the input's metadata.

    Returns:
        The conversion to apply before scoring.
    """
    if is_pq_video(color_trc):
        return LinearisationMode.PQ
    if is_hdr_video(color_trc):
        return LinearisationMode.HLG
    return LinearisationMode.SDR


def missing_colour_tags(info: VideoInfo) -> list[str]:
    """Name the colour tags an input does not declare.

    Args:
        info: Probed metadata for the input.

    Returns:
        Tag names in the order transfer, primaries, matrix. Empty when the
        input declares all three.
    """
    absent: list[str] = []
    if not info.color_trc:
        absent.append("transfer")
    if not info.color_primaries:
        absent.append("primaries")
    if not info.color_space:
        absent.append("matrix")
    return absent


def missing_mastering_display(info: VideoInfo) -> bool:
    """Say whether the probe declares no usable mastering display.

    The lossless reference takes its metadata from x265, which writes it
    from this probe. So when the probe yields no ``--master-display``
    argument the reference carries no mastering display properties, and
    ``tonemap.BT2390`` has no peak luminance to build its curve from.

    A profile that names ``master-display`` in its own settings supplies its
    own value, so that profile's encodes carry one whether or not the probe
    did. Assuming a peak still covers both clips, since ``BT2390`` takes an
    explicit ``src_max`` over whatever the properties hold.

    Args:
        info: Probed metadata for the input.

    Returns:
        True when the mastering display metadata is absent or unparseable.
    """
    arg = master_display_arg(
        info.mastering_display_color_primaries,
        info.mastering_display_luminance,
    )
    return arg is None


# Written only for the tags a partially- or un-tagged input actually omits.
# BT.709 is the right guess for an SDR input with no tags at all - it is what
# every other tool in the chain assumes for an untagged input. It is the
# WRONG guess for an HDR one: a PQ or HLG input that omits its primaries or
# matrix is overwhelmingly BT.2020, not BT.709, so those modes fall back to
# BT.2020 instead. A missing transfer always selects SDR mode -
# linearisation_mode(None) returns SDR - so the transfer fallback is only
# ever reachable there; the PQ and HLG tables below correctly have no
# transfer entry, and a lookup that needed one would be a bug upstream.
#
# Writing the properties rather than passing resize's `_in` arguments makes
# the assumption visible in the frame properties, and is the only thing that
# works: a present property beats those arguments, and an absent one is what
# is being fixed.
_TAG_PROPS = {"transfer": "_Transfer", "primaries": "_Primaries", "matrix": "_Matrix"}

_FALLBACK_VALUES: dict[LinearisationMode, dict[str, int]] = {
    LinearisationMode.SDR: {"transfer": 1, "primaries": 1, "matrix": 1},
    LinearisationMode.PQ: {"primaries": 9, "matrix": 9},
    LinearisationMode.HLG: {"primaries": 9, "matrix": 9},
}


def _pq_chain(*, assumed_peak: int | None) -> str:
    """Emit the PQ conversion, optionally assuming a mastering display.

    Args:
        assumed_peak: Peak luminance in cd/m2 to hand the tone curve, or
            None to let it read the mastering display properties.

    Returns:
        The body of ``prepare`` for a PQ input.
    """
    bounds = "" if assumed_peak is None else f"src_max={assumed_peak}, src_min=0, "
    return f"""\
    lin = core.resize.Bicubic(clip, format=vs.RGBS, transfer_s="linear",
                              primaries_s="2020", range_s="full",
                              nominal_luminance={SSIM2_NOMINAL_LUMINANCE})
    lin = core.tonemap.BT2390(
        lin, {bounds}nominal_luminance={SSIM2_NOMINAL_LUMINANCE}
    )
    return core.tonemap.BT2407(lin)"""


# The signal must reach tonemap.HLG still HLG-encoded. The OOTF applies one
# scalar derived from scene luminance to all three channels together, which a
# per-channel transfer conversion cannot reproduce: zimg's own std-b67 raises
# each channel separately and is 76% too bright on saturated blue.
_HLG_CHAIN = f"""\
    sig = core.resize.Bicubic(clip, format=vs.RGBS, transfer_s="std-b67",
                              primaries_s="2020", range_s="full")
    lin = core.tonemap.HLG(sig, nominal_luminance={SSIM2_NOMINAL_LUMINANCE})
    lin = core.tonemap.BT2390(lin, nominal_luminance={SSIM2_NOMINAL_LUMINANCE})
    return core.tonemap.BT2407(lin)"""

_SDR_CHAIN = """\
    return core.resize.Bicubic(clip, format=vs.RGBS, transfer_s="linear",
                               primaries_s="709", range_s="full")"""

# PQ is built by _pq_chain instead, since it varies with whether the mastering
# display properties will be there for the tone curve to read.
_CHAINS = {
    LinearisationMode.HLG: _HLG_CHAIN,
    LinearisationMode.SDR: _SDR_CHAIN,
}


def colour_preamble(
    mode: LinearisationMode,
    *,
    missing: Sequence[str],
    mastering_missing: bool = False,
) -> str:
    """Emit the VapourSynth source defining ``prepare(clip)``.

    ``prepare`` converts a tagged clip to linear light in BT.709 primaries,
    full-range RGBS, tonemapping first when the input is HDR.

    Only the output side of each conversion is stated. The input transfer,
    primaries, matrix and range come from the frame properties, which the
    source filter attaches from the container; zimg treats the ``_in``
    arguments as fallbacks for an absent property, so passing them on a tagged
    clip would have no effect.

    ``src_min`` and ``src_max`` are not passed to ``BT2390`` while the
    mastering display properties carry them - from the input for PQ, written
    by ``tonemap.HLG`` for HLG. Supplying them by hand defeats the plugin's
    contract check, which is what catches a mis-wired chain. They are passed
    only when ``mastering_missing`` says the properties will not be there,
    where the alternative is the filter raising and the metric being dropped.

    Args:
        mode: Which conversion the input's transfer requires.
        missing: Tag names to write a fallback value for, from
            ``missing_colour_tags``. A tag that is present is left alone;
            only the ones actually absent get overwritten. Empty means the
            input is fully tagged, and no ``SetFrameProps`` call is emitted.
        mastering_missing: True when the clips will carry no mastering
            display metadata, from ``missing_mastering_display``. A PQ input
            then hands the tone curve an assumed peak and a black of 0,
            which it would otherwise raise for want of. Ignored for HLG,
            whose peak the plugin already defaults, and for SDR, which never
            tonemaps.

    Returns:
        Python source defining ``prepare(clip)``, for embedding in a .vpy.
    """
    lines = [
        "def prepare(clip):",
        '    """Convert a tagged clip to linear RGBS, tonemapping HDR first."""',
    ]
    if missing:
        values = _FALLBACK_VALUES[mode]
        assigns = ", ".join(f"{_TAG_PROPS[tag]}={values[tag]}" for tag in missing)
        lines.append(f"    clip = core.std.SetFrameProps(clip, {assigns})")
    if mode is LinearisationMode.PQ:
        peak = SSIM2_ASSUMED_MASTERING_PEAK if mastering_missing else None
        lines.append(_pq_chain(assumed_peak=peak))
    else:
        lines.append(_CHAINS[mode])
    return "\n".join(lines) + "\n"
