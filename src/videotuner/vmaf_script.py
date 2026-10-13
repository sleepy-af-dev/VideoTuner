"""The VapourSynth script VMAF reads its frames through.

VMAF lives in ffmpeg and takes two inputs, while ``vspipe`` writes one
stdout. ``vspipe -c mkv`` resolves that: it writes every output the script
sets into one matroska stream, which ffmpeg demuxes as two video tracks.

The reference is the sampling script itself, so the frames VMAF scores
against are selected by the same generator the encoder is fed rather than
read back from a copy of them. The distorted encode is loaded through the
selected indexer, and both go through the same ``prepare``, so they cannot
be tonemapped differently from each other.

For an HDR input whose metrics share samples, the same script can also score
SSIMULACRA2: both metrics then read frames tonemapped once, and each
SSIMULACRA2 score leaves through a file, since vspipe has no other channel
out of a two-track pipe.
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
    vmaf_encode_source,
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
    ssim2_scores: Path | None = None,
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
        ssim2_scores: When given, SSIMULACRA2 is scored in the same pass and
            each frame's score is written to this file, for
            ``read_ssim2_scores``. HDR inputs only.

    Returns:
        The .vpy contents, with the reference as output 0 and the distorted
        encode as output 1.

    Raises:
        ValueError: If ``ssim2_scores`` is given for an SDR input.
    """
    mode = linearisation_mode(info.color_trc)
    is_pq = mode is LinearisationMode.PQ
    spec = indexer_spec(indexer)
    dis_expr = spec.source_expr(str(dis_path.resolve()), str(dis_cache))

    if ssim2_scores is not None:
        if mode is LinearisationMode.SDR:
            raise ValueError(
                "Only an HDR input can score both metrics in one pass: an SDR "
                + "clip goes to VMAF unconverted, not as SSIMULACRA2's frames"
            )
        preamble = colour_preamble(
            mode,
            missing=missing_colour_tags(info),
            mastering_missing=is_pq and missing_mastering_display(info),
        )
        return f"""{ref_script}

{preamble}
{vmaf_encode_source()}
ref = prepare(sampled)
dis = prepare({dis_expr})

# One tonemapping pass for both metrics: SSIMULACRA2 scores the linear pair,
# and VMAF takes the same frames encoded back to SDR video.
ssim2 = ref.vszip.SSIMULACRA2(dis)

# vspipe cannot carry a frame property out of a two-track mkv, so each score
# is written to a file as its frame goes to ffmpeg.
_ssim2_scores = open({str(ssim2_scores)!r}, "w", encoding="utf-8")


def _record_ssim2(n, f):
    _ssim2_scores.write(f"{{n}}:{{f[1].props['SSIMULACRA2']!r}}\\n")
    _ssim2_scores.flush()
    return f[0]


vref = encode_for_vmaf(ref)
vref = core.std.ModifyFrame(vref, clips=[vref, ssim2], selector=_record_ssim2)

# Output order is the contract with ffmpeg's -lavfi, which reads the
# reference as [0:v:0] and the distorted encode as [0:v:1]. Registering
# output 0 again replaces the sampling script's own.
vref.set_output(0)
encode_for_vmaf(dis).set_output(1)
"""

    preamble = colour_preamble(
        mode,
        missing=missing_colour_tags(info),
        mastering_missing=is_pq and missing_mastering_display(info),
        target=ColourTarget.VMAF,
    )

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


def read_ssim2_scores(path: Path) -> list[float]:
    """Read the per-frame SSIMULACRA2 scores a combined pass wrote.

    Frames are requested concurrently, so lines arrive out of order, and a
    frame evicted from the cache and requested again is written twice with
    the same score. Keying on the frame number handles both.

    Args:
        path: The file passed as ``ssim2_scores`` when the script was built.

    Returns:
        One score per frame, in frame order.

    Raises:
        RuntimeError: If the pass wrote no scores file.
    """
    if not path.exists():
        raise RuntimeError(f"The combined pass wrote no SSIMULACRA2 scores to {path}")
    by_frame: dict[int, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        frame, _, score = line.partition(":")
        by_frame[int(frame)] = float(score)
    return [by_frame[n] for n in sorted(by_frame)]
