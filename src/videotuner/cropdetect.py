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

import logging
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .constants import CROPDETECT_MIN_SAMPLES
from .encoding_utils import (
    CropValues,
    IndexerType,
    VapourSynthEnv,
    build_vspipe_command,
    indexer_spec,
    resolve_absolute_path,
    run_vspipe_encode,
    write_vpy_script,
)
from .metric_colour import (
    ColourTarget,
    LinearisationMode,
    colour_preamble,
    linearisation_mode,
    missing_colour_tags,
    missing_mastering_display,
)
from .tool_parsers import CROPDETECT_RE
from .utils import warn_user

if TYPE_CHECKING:
    from .media import VideoInfo

logger = logging.getLogger(__name__)


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


def _crops_in(line: str) -> list[tuple[str, str, str, str]]:
    """Every ``crop=W:H:X:Y`` in one line, as its four unparsed groups.

    ``re.findall`` is typed as returning ``list[Any]``, so naming the shape
    once here keeps both callers below type-checked rather than each unpacking
    four values the checker knows nothing about.
    """
    return cast(list[tuple[str, str, str, str]], CROPDETECT_RE.findall(line))


def accumulate_crop(
    lines: Iterable[str], *, source_width: int, source_height: int
) -> CropValues:
    """Reduce ffmpeg's per-frame crop lines to one crop.

    The smallest crop per edge wins. A dark scene makes cropdetect read the
    picture itself as black and report a crop far larger than the bar, so only
    what every analysed frame agrees is black is cropped away. The bias is
    downward on purpose: leaving a few black rows is recoverable, cutting
    picture is not.

    Args:
        lines: Output lines from ffmpeg, in any order.
        source_width: Width of the source in pixels.
        source_height: Height of the source in pixels.

    Returns:
        The combined crop, or no crop when nothing was detected.
    """
    detected = [
        (int(w), int(h), int(x), int(y))
        for line in lines
        for w, h, x, y in _crops_in(line)
    ]
    if not detected:
        return CropValues(left=0, right=0, top=0, bottom=0)

    return CropValues(
        left=max(0, min(x for _, _, x, _ in detected)),
        top=max(0, min(y for _, _, _, y in detected)),
        right=max(0, min(source_width - w - x for w, _, x, _ in detected)),
        bottom=max(0, min(source_height - h - y for _, h, _, y in detected)),
    )


def make_crop_handler(
    captured: list[str],
    stop: threading.Event,
    *,
    source_width: int,
    source_height: int,
    line_handler: Callable[[str], bool] | None,
) -> Callable[[str], bool]:
    """Build the line handler that collects crops and decides when to stop.

    Crops are collected as they stream rather than read from the return value,
    which is what lets the run end before ffmpeg has finished.

    The pipe stops as soon as one frame reports no crop on any edge. That is
    exact rather than an approximation: crops combine with ``min`` and clamp at
    zero, so such a frame has already fixed the result at no crop, and no later
    frame can lower it.

    Args:
        captured: List the handler appends matching lines to.
        stop: Event the handler sets when the answer can no longer change.
        source_width: Width of the source in pixels.
        source_height: Height of the source in pixels.
        line_handler: The caller's own handler, usually the progress bar's.
            Every line still reaches it.

    Returns:
        A handler suitable for ``run_vspipe_encode``.
    """

    def handler(line: str) -> bool:
        for w_s, h_s, x_s, y_s in _crops_in(line):
            captured.append(line)
            w, h, x, y = int(w_s), int(h_s), int(x_s), int(y_s)
            if (
                x <= 0
                and y <= 0
                and source_width - w - x <= 0
                and source_height - h - y <= 0
            ):
                stop.set()
        return bool(line_handler(line)) if line_handler is not None else False

    return handler


def calculate_cropdetect_values(
    source_path: Path,
    start_frame: int,
    num_frames: int,
    fps: float,
    *,
    info: VideoInfo,
    cache_file: Path,
    indexer: IndexerType,
    interval: int = 30,
    ffmpeg_bin: str = "ffmpeg",
    source_width: int,
    source_height: int,
    cwd: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
    cropdetect_mode: str = "black",
    cropdetect_limit: int | None = None,
    cropdetect_round: int = 2,
    cropdetect_mv_threshold: int | None = None,
    cropdetect_low: float | None = None,
    cropdetect_high: float | None = None,
) -> CropValues:
    """Detect the crop by piping selected frames into ffmpeg's cropdetect.

    VapourSynth reads the source through the selected indexer, picks the sample
    frames by number, tonemaps an HDR input, and writes 8-bit frames to one
    ffmpeg process. The 8 bits matter: ``cropdetect_limit`` is a code value, so
    its default of 24 means 24/255.

    Args:
        source_path: Input source video path.
        start_frame: First frame to analyse (0-indexed).
        num_frames: Total number of frames in the video.
        fps: Video framerate for sampling frequency.
        info: Probed metadata, which decides the colour conversion.
        cache_file: Index file for the source.
        indexer: Source filter to read the source with.
        interval: Seconds between sampled frames.
        ffmpeg_bin: Path to the ffmpeg binary.
        source_width: Width of the source video in pixels.
        source_height: Height of the source video in pixels.
        cwd: Working directory for execution.
        line_handler: Optional callback for progress parsing.
        cropdetect_mode: Detection mode (``"black"`` or ``"mvedges"``).
        cropdetect_limit: Black pixel threshold 0-255 (None = FFmpeg default 24).
        cropdetect_round: Crop dimension divisibility (default 2).
        cropdetect_mv_threshold: Motion threshold in pixels (None = FFmpeg default 8).
        cropdetect_low: Canny low threshold 0.0-1.0 (None = FFmpeg default).
        cropdetect_high: Canny high threshold 0.0-1.0 (None = FFmpeg default).

    Returns:
        CropValues with left, right, top, bottom crop values. No crop at all
        when the source yields no usable frames, when nothing was detected, or
        when the pipe failed: crop detection is an optimisation, so it degrades
        rather than ending the run.
    """
    plan = cropdetect_sample_plan(
        start_frame=start_frame, num_frames=num_frames, fps=fps, interval=interval
    )
    if plan is None:
        warn_user(logger, "No sample frames to analyse — returning zero crop")
        return CropValues(left=0, right=0, top=0, bottom=0)

    # skip=0 evaluates the very first frame; the default skip=2 would discard
    # frames we deliberately selected. reset=1 detects each frame on its own,
    # which is what makes one line per frame and lets the minimum above mean
    # what it says.
    cd_parts = [f"round={cropdetect_round}", "reset=1", "skip=0"]
    if cropdetect_mode != "black":
        cd_parts.append(f"mode={cropdetect_mode}")
    if cropdetect_limit is not None:
        cd_parts.append(f"limit={cropdetect_limit}")
    if cropdetect_mv_threshold is not None:
        cd_parts.append(f"mv_threshold={cropdetect_mv_threshold}")
    if cropdetect_low is not None:
        cd_parts.append(f"low={cropdetect_low}")
    if cropdetect_high is not None:
        cd_parts.append(f"high={cropdetect_high}")

    crop_lines: list[str] = []
    stop = threading.Event()
    handler = make_crop_handler(
        crop_lines,
        stop,
        source_width=source_width,
        source_height=source_height,
        line_handler=line_handler,
    )

    with tempfile.TemporaryDirectory(prefix="videotuner-cropdetect-") as tmp_dir:
        vpy_path = Path(tmp_dir) / "cropdetect.vpy"
        write_vpy_script(
            vpy_path,
            build_cropdetect_vpy_script(
                source_path=source_path,
                cache_file=cache_file,
                indexer=indexer,
                info=info,
                plan=plan,
                cwd=cwd,
            ),
        )
        vs_env = VapourSynthEnv.from_cwd(cwd)
        ffmpeg_args = [
            ffmpeg_bin,
            "-hide_banner",
            "-stats",
            "-f",
            "yuv4mpegpipe",
            "-i",
            "pipe:0",
            "-vf",
            f"cropdetect={':'.join(cd_parts)}",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]
        try:
            _ = run_vspipe_encode(
                vspipe_args=build_vspipe_command(vs_env, vpy_path, cwd),
                encoder_args=ffmpeg_args,
                vs_env=vs_env.build_env(),
                cwd=cwd,
                line_handler=handler,
                stop=stop,
            )
        except RuntimeError as e:
            warn_user(logger, "Crop detection failed; continuing with no crop: %s", e)
            return CropValues(left=0, right=0, top=0, bottom=0)

    if not crop_lines:
        warn_user(logger, "cropdetect produced no output — returning zero crop")
        return CropValues(left=0, right=0, top=0, bottom=0)

    values = accumulate_crop(
        crop_lines, source_width=source_width, source_height=source_height
    )
    logger.debug(
        "CropDetect values calculated: L=%d R=%d T=%d B=%d",
        values.left,
        values.right,
        values.top,
        values.bottom,
    )
    return values
