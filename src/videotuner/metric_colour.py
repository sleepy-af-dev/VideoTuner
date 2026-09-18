"""Colour handling shared by the SSIMULACRA2 and VMAF assessments.

SSIMULACRA2 is an SDR metric: its weights were fitted on SDR still-image
datasets and it has no concept of absolute luminance. The reference
implementation scores in linear light, colour-managed from whatever encoding
the input declares.

vszip does neither. It overwrites ``_Transfer`` with sRGB before linearising
and never reads ``_Primaries``, so an HDR input is linearised through the wrong
curve and scored in a domain the metric was never fitted for.

This module emits the VapourSynth source that tonemaps a tagged clip and
hands it to each metric the way it wants it: vszip gets linear RGBS, where
both of its conversion paths early-return on such a clip - RGBS skips its
``toRGBS``, and a linear transfer skips its sRGB step - so the pre-converted
frames reach the metric untouched. VMAF gets the same chain's output encoded
back to limited-range BT.709 YUV, which is what it was trained on.
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


#: Label for the operator that takes an HDR input to SDR before scoring. Both
#: VMAF and SSIMULACRA2 share it: neither metric is defined on HDR, so an HDR
#: input always goes through it before either one scores it.
SSIM2_TONEMAP_BACKEND = "vs-tonemap (BT.2390 + BT.2407)"


class LinearisationMode(StrEnum):
    """Which conversion an input's transfer requires."""

    PQ = "pq"
    HLG = "hlg"
    SDR = "sdr"


class MetricTarget(StrEnum):
    """Which metric the converted frames are for.

    Both metrics share the tonemapping front and differ only in what they
    want handed to them: SSIMULACRA2 scores in linear light, VMAF scores
    ordinary SDR video.
    """

    SSIM2 = "ssim2"
    VMAF = "vmaf"


# SSIMULACRA2 takes the front's own output: vszip's two conversion paths both
# early-return on a linear RGBS clip. VMAF is trained on BT.709 SDR video and
# reads code values rather than light, so an HDR clip is encoded back the way
# an SDR deliverable is once the tone curve has run. Range is load-bearing and
# silent when wrong: the front ends full-range RGBS and this must land
# limited-range YUV, which is what libvmaf is handed.
#
# Only reached for HDR. An SDR clip bound for VMAF is already in the form VMAF
# wants and returns unconverted, which `colour_preamble` handles before it gets
# this far.
_TAILS = {
    MetricTarget.SSIM2: "    return out",
    MetricTarget.VMAF: (
        "    return core.resize.Bicubic(out, format=vs.YUV420P10,\n"
        '                               transfer_s="709", matrix_s="709",\n'
        '                               range_s="limited")'
    ),
}


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
    out = core.tonemap.BT2407(lin)"""


# The signal must reach tonemap.HLG still HLG-encoded. The OOTF applies one
# scalar derived from scene luminance to all three channels together, which a
# per-channel transfer conversion cannot reproduce: zimg's own std-b67 raises
# each channel separately and is 76% too bright on saturated blue.
_HLG_CHAIN = f"""\
    sig = core.resize.Bicubic(clip, format=vs.RGBS, transfer_s="std-b67",
                              primaries_s="2020", range_s="full")
    lin = core.tonemap.HLG(sig, nominal_luminance={SSIM2_NOMINAL_LUMINANCE})
    lin = core.tonemap.BT2390(lin, nominal_luminance={SSIM2_NOMINAL_LUMINANCE})
    out = core.tonemap.BT2407(lin)"""

_SDR_CHAIN = """\
    out = core.resize.Bicubic(clip, format=vs.RGBS, transfer_s="linear",
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
    target: MetricTarget = MetricTarget.SSIM2,
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
        target: Which metric the frames are for. Both share the tonemapping
            front; SSIMULACRA2 takes its linear RGBS output directly, while
            VMAF has it encoded to limited-range BT.709 YUV.

    Returns:
        Python source defining ``prepare(clip)``, for embedding in a .vpy.
    """
    # The emitted docstring describes what this particular chain does, since a
    # reader meets it in a generated .vpy with no other context.
    if mode is LinearisationMode.SDR and target is MetricTarget.VMAF:
        summary = "Tag a clip VMAF can score as it stands."
    elif target is MetricTarget.VMAF:
        summary = "Tonemap an HDR clip to SDR and encode it back for VMAF."
    else:
        summary = "Convert a tagged clip to linear RGBS, tonemapping HDR first."
    lines = [
        "def prepare(clip):",
        f'    """{summary}"""',
    ]
    if missing:
        values = _FALLBACK_VALUES[mode]
        assigns = ", ".join(f"{_TAG_PROPS[tag]}={values[tag]}" for tag in missing)
        lines.append(f"    clip = core.std.SetFrameProps(clip, {assigns})")
    if mode is LinearisationMode.SDR and target is MetricTarget.VMAF:
        # Nothing to do. VMAF reads code values, and its models were fitted on
        # SDR video in its native encoded form, so the frames it wants are the
        # ones already on disk. Converting to linear RGB and back would
        # resample chroma 420-444-420, apply a 709 EOTF/OETF pair, convert
        # range twice and requantise - every step lossy, and none of it buying
        # anything when nothing is being tonemapped. It also smooths both
        # clips alike, which makes them more similar to each other, which
        # flatters the score: measured at 0.04 to 0.07 VMAF points upward.
        #
        # Depth is still handled downstream: build_vmaf_filter emits
        # format=yuv420p10le in ffmpeg either way.
        #
        # A tag written above still applies - the passthrough skips the
        # conversion, not the tagging, and vspipe -c mkv carries those
        # properties into the stream ffmpeg reads.
        lines.append("    return clip")
        return "\n".join(lines) + "\n"

    if mode is LinearisationMode.PQ:
        peak = SSIM2_ASSUMED_MASTERING_PEAK if mastering_missing else None
        lines.append(_pq_chain(assumed_peak=peak))
    else:
        lines.append(_CHAINS[mode])
    lines.append(_TAILS[target])
    return "\n".join(lines) + "\n"


def colour_assumption_notes(info: VideoInfo) -> list[str]:
    """Describe what the shared colour chain has to assume for an input.

    Both assumptions follow from the probe alone and hold for a whole run,
    so a caller reports them once rather than once per assessment.

    Args:
        info: Probed metadata for the input.

    Returns:
        One message per assumption, in the order the chain makes them. Empty
        when the input declares everything the chain needs.
    """
    mode = linearisation_mode(info.color_trc)
    notes: list[str] = []

    absent = missing_colour_tags(info)
    if absent:
        # Naming what was assumed, not only what was missing, is what a prior
        # defect here lacked: it silently reset an HDR input's correct
        # transfer to BT.709 and the warning gave no way to notice.
        assumed = "BT.709" if mode is LinearisationMode.SDR else "BT.2020"
        notes.append(
            f"Input declares no {', '.join(absent)}; assuming {assumed} for "
            "VMAF and SSIMULACRA2. Scores stay comparable between encodes "
            "of this input, but not against a differently tagged one."
        )

    # HLG needs no equivalent: tonemap.HLG defaults its own peak and writes
    # the properties BT2390 then reads.
    if mode is LinearisationMode.PQ and missing_mastering_display(info):
        notes.append(
            "Input declares no mastering display; assuming a "
            f"{SSIM2_ASSUMED_MASTERING_PEAK} cd/m2 peak for VMAF and "
            "SSIMULACRA2. Scores stay comparable between encodes of this "
            "input, but not against one that declares its own peak."
        )

    return notes
