"""
Centralized x265 encoder parameter building and validation.

This module provides a single source of truth for format-specific x265 parameters
that are auto-detected from source video but can be overridden in user profiles.
"""

from __future__ import annotations

import logging

from .encoding_utils import MATRIX_FOR_PRIMARIES, is_hdr_video, is_pq_video
from .media import VideoInfo, get_bit_depth_from_pix_fmt

logger = logging.getLogger(__name__)

# Global x265 parameters that are auto-detected from source video by default.
# These can be overridden in user profiles if needed.
GLOBAL_X265_PARAMS = {
    "colorprim",
    "transfer",
    "colormatrix",
    "hdr10",
    "hdr10-opt",
    "master-display",
    "max-cll",
    "chromaloc",
    "output-depth",
    "repeat-headers",
    "aud",
    "hrd",
    "range",
}

# Valid x265 preset values
VALID_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
)


def build_global_x265_params(
    video_info: VideoInfo,
    is_lossless: bool = False,
    chroma_location: int | None = None,
    skip_params: set[str] | None = None,
) -> list[str]:
    """
    Build global x265 parameters from video metadata in CLI format for x265.exe.

    These parameters are auto-detected from the source video and include:
    - Color space parameters (colorprim, transfer, colormatrix, range)
    - HDR metadata (hdr10, master-display, max-cll)
    - Format compatibility (output-depth, chromaloc, repeat-headers, aud)

    Args:
        video_info: MediaInfo object from ffprobe
        is_lossless: If True, adds --lossless flag
        chroma_location: Chroma sample location (0-5), auto-detected if None
        skip_params: Set of parameter names to skip (for profile overrides)

    Returns:
        List of x265 CLI arguments (e.g., ["--colorprim", "bt2020", "--hdr10"])
    """
    x265_params: list[str] = []
    skip = skip_params or set()

    # Lossless encoding
    if is_lossless:
        x265_params.append("--lossless")

    # Universal parameters for all encodes
    if "aud" not in skip and "no-aud" not in skip:
        x265_params.append("--aud")  # Access Unit Delimiters (boolean flag)

    # HRD (Hypothetical Reference Decoder) information for VBV compliance
    # Only enable for non-lossless encodes (lossless has no rate control)
    if not is_lossless and "hrd" not in skip and "no-hrd" not in skip:
        x265_params.append("--hrd")

    # Detect if content is HDR (PQ/SMPTE 2084 or HLG/ARIB STD-B67 transfer)
    color_trc = video_info.color_trc
    is_hdr = is_hdr_video(color_trc)
    is_pq = is_pq_video(color_trc)

    logger.debug(
        "HDR detection: color_trc='%s', is_hdr=%s, is_pq=%s", color_trc, is_hdr, is_pq
    )

    # Determine output bit depth from source pixel format
    if "output-depth" not in skip:
        output_depth = get_bit_depth_from_pix_fmt(video_info.pix_fmt)
        x265_params.extend(["--output-depth", str(output_depth)])

    # Add repeat-headers: required for HDR, disabled for SDR
    if "repeat-headers" not in skip and "no-repeat-headers" not in skip:
        if is_hdr:
            x265_params.append("--repeat-headers")
        else:
            x265_params.append("--no-repeat-headers")

    # HDR10 signalling is PQ only. x265 documents --hdr10 as controlling the
    # HDR10 SEI packet, which carries the mastering display and MaxCLL an HLG
    # source does not have, and --hdr10-opt as a block-level optimisation for
    # HDR10 content. HLG is HDR but not HDR10, so it gets neither.
    if "hdr10" not in skip and "no-hdr10" not in skip:
        if is_pq:
            x265_params.append("--hdr10")
        else:
            x265_params.append("--no-hdr10")

    if "hdr10-opt" not in skip and "no-hdr10-opt" not in skip:
        if is_pq:
            x265_params.append("--hdr10-opt")

    # These fields carry ffprobe's vocabulary, which already names primaries and
    # transfers the way x265 does, so the value passes straight through.
    if "colorprim" not in skip and video_info.color_primaries:
        x265_params.extend(["--colorprim", video_info.color_primaries])

    if "transfer" not in skip and color_trc:
        x265_params.extend(["--transfer", color_trc])

    # Map color matrix
    # Infer from primaries if color_space is not a standard matrix identifier
    if "colormatrix" not in skip:
        colormatrix = None
        color_space = video_info.color_space
        if color_space:
            colormatrix_map = {
                "bt709": "bt709",
                "bt2020nc": "bt2020nc",
                "bt2020c": "bt2020c",
                "smpte170m": "smpte170m",
                "smpte240m": "smpte240m",
                "bt470bg": "bt470bg",
            }
            colormatrix = colormatrix_map.get(color_space)

        # Fallback: infer the matrix that goes with the primaries
        if colormatrix is None and video_info.color_primaries:
            colormatrix = MATRIX_FOR_PRIMARIES.get(video_info.color_primaries)

        logger.debug(
            f"Color matrix detection: color_space='{video_info.color_space}', colormatrix='{colormatrix}'"  # noqa: E501  # TODO(E501): shorten line
        )

        if colormatrix:
            x265_params.extend(["--colormatrix", colormatrix])

    # Preserve color range
    if "range" not in skip and video_info.color_range:
        range_val = "limited" if video_info.color_range == "tv" else "full"
        x265_params.extend(["--range", range_val])

    # Preserve chroma location
    if "chromaloc" not in skip and chroma_location is not None:
        x265_params.extend(["--chromaloc", str(chroma_location)])

    # Preserve HDR mastering display metadata if present
    if "master-display" not in skip and (
        video_info.mastering_display_color_primaries
        and video_info.mastering_display_luminance
    ):
        from .utils import parse_master_display_metadata

        master_display = parse_master_display_metadata(
            video_info.mastering_display_color_primaries,
            video_info.mastering_display_luminance,
        )
        if master_display:
            x265_params.extend(["--master-display", master_display])
            logger.debug("Adding mastering display metadata: %s", master_display)

    # Preserve MaxCLL/MaxFALL if present
    if "max-cll" not in skip and (
        video_info.maximum_content_light_level
        and video_info.maximum_frameaverage_light_level
    ):
        max_cll_val = video_info.maximum_content_light_level.replace(
            " cd/m2", ""
        ).strip()
        max_fall_val = video_info.maximum_frameaverage_light_level.replace(
            " cd/m2", ""
        ).strip()
        x265_params.extend(["--max-cll", f"{max_cll_val},{max_fall_val}"])
        logger.debug("Adding MaxCLL/MaxFALL: %s,%s", max_cll_val, max_fall_val)

    return x265_params
