"""The VapourSynth script VMAF reads its frames through.

VMAF lives in ffmpeg and takes two inputs, while ``vspipe`` writes one
stdout. ``vspipe -c mkv`` resolves that: it writes every output the script
sets into one matroska stream, which ffmpeg demuxes as two video tracks.

The reference is the sampling script itself, so the frames VMAF scores
against are selected by the same generator the encoder is fed rather than
read back from a copy of them. The distorted encode is loaded through the
selected indexer, and both go through the same ``prepare``, so they cannot
be tonemapped differently from each other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .encoding_utils import IndexerType, indexer_spec
from .metric_colour import (
    ColourTarget,
    LinearisationMode,
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
    ref_script: str,
    dis_path: Path,
    dis_cache: Path,
    indexer: IndexerType,
    info: VideoInfo,
) -> str:
    """Build the script that feeds VMAF both clips down one pipe.

    Args:
        ref_script: Output of ``build_sampling_vpy_script``, which imports
            VapourSynth, binds the selected frames to ``sampled`` and sets
            them as output 0.
        dis_path: Distorted encode.
        dis_cache: Index file for the distorted encode.
        indexer: Source filter to load the distorted encode with.
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
        target=ColourTarget.VMAF,
    )
    spec = indexer_spec(indexer)
    dis_expr = spec.source_expr(str(dis_path.resolve()), str(dis_cache))

    return f"""{ref_script}

{preamble}
ref = sampled
dis = {dis_expr}

ref = prepare(ref)
dis = prepare(dis)

# Output order is the contract with ffmpeg's -lavfi, which reads the
# reference as [0:v:0] and the distorted encode as [0:v:1]. The sampling
# script above already set its own clip as output 0; registering an index
# again replaces it, so what reaches ffmpeg is the converted pair.
ref.set_output(0)
dis.set_output(1)
"""
