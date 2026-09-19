"""Crop detection: VapourSynth decodes and tonemaps, ffmpeg detects.

No bundled VapourSynth plugin detects letterboxing, so ffmpeg's ``cropdetect``
filter stays. What moves is everything in front of it. ``vspipe`` reads the
source through the selected indexer, picks the sample frames by number, runs
the colour chain both metrics use, and writes 8-bit frames down one pipe.

That closes three gaps at once. ``--indexer`` reaches crop detection for the
first time. The tone curve is the one the metrics use rather than whichever of
libplacebo or zscale the machine happened to support. And one process replaces
one per sampled frame, which on a long source was several hundred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .constants import CROPDETECT_MIN_SAMPLES
from .encoding_utils import IndexerType, indexer_spec, resolve_absolute_path
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


@dataclass(frozen=True)
class CropdetectPlan:
    """Which frames crop detection will look at.

    Attributes:
        start: First frame to analyse, inclusive.
        end: One past the last frame to analyse.
        step: Frames between samples.
    """

    start: int
    end: int
    step: int

    @property
    def count(self) -> int:
        """How many frames the plan selects."""
        return -(-(self.end - self.start) // self.step)


def cropdetect_sample_plan(
    *,
    start_frame: int,
    num_frames: int,
    fps: float,
    interval: int,
) -> CropdetectPlan | None:
    """Choose the frames to analyse, with a floor under how many.

    The first and last tenth are skipped, so intros and credits cannot decide
    the crop. Within what is left, one frame is taken every ``interval``
    seconds.

    The step is then lowered if that would yield fewer than
    ``CROPDETECT_MIN_SAMPLES`` frames. It is never raised: a long source keeps
    the interval it was asked for.

    Args:
        start_frame: First frame of the source, 0-indexed.
        num_frames: Total frames in the source.
        fps: Frame rate, for converting the interval to frames.
        interval: Seconds between samples.

    Returns:
        The plan, or None when the guard bands leave no frames at all. A
        caller that gets None reports no crop rather than building a script
        that selects nothing.
    """
    skip = max(1, int(num_frames * 0.10))
    start = start_frame + skip
    end = start_frame + num_frames - skip
    if end <= start:
        return None

    step = max(1, round(fps * interval))
    step = min(step, max(1, (end - start) // CROPDETECT_MIN_SAMPLES))
    return CropdetectPlan(start=start, end=end, step=step)


def build_cropdetect_vpy_script(
    *,
    source_path: Path,
    cache_file: Path,
    indexer: IndexerType,
    info: VideoInfo,
    plan: CropdetectPlan,
    cwd: Path | None = None,
) -> str:
    """Build the script that feeds ffmpeg's cropdetect its frames.

    Args:
        source_path: Input source video.
        cache_file: Index file for that source.
        indexer: Source filter to load it with.
        info: Probed metadata, which decides the conversion.
        plan: Frames to analyse, from ``cropdetect_sample_plan``.
        cwd: Working directory for path resolution.

    Returns:
        The .vpy contents, registering one output of 8-bit YUV 4:2:0.
    """
    mode = linearisation_mode(info.color_trc)
    preamble = colour_preamble(
        mode,
        missing=missing_colour_tags(info),
        mastering_missing=(
            mode is LinearisationMode.PQ and missing_mastering_display(info)
        ),
        target=ColourTarget.CROPDETECT,
    )
    load = indexer_spec(indexer).source_expr(
        str(resolve_absolute_path(source_path, cwd)),
        str(resolve_absolute_path(cache_file, cwd)),
    )

    return f"""import vapoursynth as vs
core = vs.core

{preamble}
clip = {load}

# Trim before selecting. Selecting first would count from frame 0 and land on
# a different set of pictures.
clip = clip[{plan.start}:{plan.end}]
clip = core.std.SelectEvery(clip, cycle={plan.step}, offsets=[0])

# One output: ffmpeg reads y4m from a single stdout, so a second would be
# written into the same stream and read as trailing garbage.
prepare(clip).set_output()
"""
