"""The VapourSynth script VMAF reads its frames through.

VMAF lives in ffmpeg and takes two inputs, while ``vspipe`` writes one
stdout. ``vspipe -c mkv`` resolves that: it writes every output the script
sets into one matroska stream, which ffmpeg demuxes as two video tracks.

Both clips are loaded through the selected indexer and converted by the same
``prepare``, so the reference and the distorted encode cannot be tonemapped
differently from each other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .encoding_utils import IndexerType, indexer_spec
from .metric_colour import (
    LinearisationMode,
    MetricTarget,
    colour_preamble,
    linearisation_mode,
    missing_colour_tags,
    missing_mastering_display,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .media import VideoInfo


def build_vmaf_vpy_script(
    *,
    ref_path: Path,
    dis_path: Path,
    ref_cache: Path,
    dis_cache: Path,
    indexer: IndexerType,
    info: VideoInfo,
) -> str:
    """Build the script that feeds VMAF both clips down one pipe.

    Args:
        ref_path: Reference video.
        dis_path: Distorted encode.
        ref_cache: Index file for the reference.
        dis_cache: Index file for the distorted encode.
        indexer: Source filter to load both clips with.
        info: Probed metadata for the input, which decides the conversion.

    Returns:
        The .vpy contents, with the reference as output 0 and the distorted
        encode as output 1.
    """
    mode = linearisation_mode(info.color_trc)
    is_pq = mode is LinearisationMode.PQ
    preamble = colour_preamble(
        mode,
        missing=missing_colour_tags(info),
        mastering_missing=is_pq and missing_mastering_display(info),
        target=MetricTarget.VMAF,
    )
    spec = indexer_spec(indexer)
    ref_expr = spec.source_expr(str(ref_path.resolve()), str(ref_cache))
    dis_expr = spec.source_expr(str(dis_path.resolve()), str(dis_cache))

    return f"""import vapoursynth as vs

core = vs.core

{preamble}
ref = {ref_expr}
dis = {dis_expr}

ref = prepare(ref)
dis = prepare(dis)

# Output order is the contract with ffmpeg's -lavfi, which reads the
# reference as [0:v:0] and the distorted encode as [0:v:1].
ref.set_output(0)
dis.set_output(1)
"""
