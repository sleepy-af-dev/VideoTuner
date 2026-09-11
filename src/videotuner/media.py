from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import cast

from .encoding_utils import is_hdr_video
from .tool_parsers import get_float, get_int, get_str, parse_fraction

logger = logging.getLogger(__name__)


class InvalidVideoFileError(Exception):
    """Raised when the input file is not a valid video file."""


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    duration: float  # seconds
    pix_fmt: str | None = None
    width: int | None = None
    height: int | None = None
    color_primaries: str | None = None
    color_trc: str | None = None
    color_space: str | None = None
    color_range: str | None = None  # "tv" (limited), "pc" (full), or None
    chroma_location: int | None = None  # x265 chromaloc value (0-5)
    video_bitrate_kbps: float | None = None  # Video stream bitrate in kbps
    frame_count: int | None = None  # Total frame count from container metadata
    # HDR metadata
    mastering_display_color_primaries: str | None = None
    mastering_display_luminance: str | None = None
    maximum_content_light_level: str | None = None
    maximum_frameaverage_light_level: str | None = None


def combine_video_info(infos: Sequence[VideoInfo]) -> VideoInfo:
    """Describe several files as the one source a job reads.

    Callers must already have established that the files agree on resolution,
    frame rate and HDR status, so the format facts are taken from the first.
    Only the quantities that accumulate are recomputed.

    Bitrate is weighted by duration so a short high-bitrate file cannot outweigh
    a long one, and files that report no bitrate are left out of the weighting
    rather than counted as zero. Frame count is only known if every file
    reported one.

    Args:
        infos: One entry per file, in playback order

    Returns:
        A single VideoInfo describing the joined source
    """
    first = infos[0]
    if len(infos) == 1:
        return first

    total_duration = sum(i.duration for i in infos)

    frame_counts = [i.frame_count for i in infos]
    total_frames = (
        sum(c for c in frame_counts if c is not None)
        if all(c is not None for c in frame_counts)
        else None
    )

    rated = [i for i in infos if i.video_bitrate_kbps is not None and i.duration > 0]
    rated_duration = sum(i.duration for i in rated)
    bitrate = (
        sum((i.video_bitrate_kbps or 0.0) * i.duration for i in rated) / rated_duration
        if rated_duration > 0
        else None
    )

    return replace(
        first,
        duration=total_duration,
        frame_count=total_frames,
        video_bitrate_kbps=bitrate,
    )


def _run_ffprobe_json(
    input_path: Path,
    stream_entries: list[str] | None = None,
    format_entries: list[str] | None = None,
    ffprobe_bin: str = "ffprobe",
) -> tuple[dict[str, object], dict[str, object]]:
    """Run ffprobe with JSON output and return first video stream and format data.

    Args:
        input_path: Path to video file
        stream_entries: Stream fields to query (e.g., ["pix_fmt", "width", "height"])
        format_entries: Format fields to query (e.g., ["duration"])
        ffprobe_bin: Path to ffprobe binary

    Returns:
        Tuple of (stream_dict, format_dict) for the first video stream.
        Empty dicts if parsing fails.
    """
    show_entries_parts: list[str] = []
    if stream_entries:
        show_entries_parts.append(f"stream={','.join(stream_entries)}")
    if format_entries:
        show_entries_parts.append(f"format={','.join(format_entries)}")

    if not show_entries_parts:
        return {}, {}

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        ":".join(show_entries_parts),
        "-of",
        "json",
        str(input_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        logger.warning("ffprobe JSON query failed: %s", proc.stderr)
        return {}, {}

    try:
        raw_data = cast(object, json.loads(proc.stdout))
        if not isinstance(raw_data, dict):
            return {}, {}
        data = cast(dict[str, object], raw_data)

        # Extract first stream
        stream_dict: dict[str, object] = {}
        streams_raw = data.get("streams", [])
        if isinstance(streams_raw, list) and streams_raw:
            first_stream = cast(object, streams_raw[0])
            if isinstance(first_stream, dict):
                stream_dict = cast(dict[str, object], first_stream)

        # Extract format
        format_dict: dict[str, object] = {}
        fmt_raw = data.get("format", {})
        if isinstance(fmt_raw, dict):
            format_dict = cast(dict[str, object], fmt_raw)

        return stream_dict, format_dict

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse ffprobe JSON: %s", e)
        return {}, {}


def estimate_frame_count(
    fps: float, *, track_duration: float | None, container_duration: float
) -> int | None:
    """Estimate a frame count for a file whose metadata carries none.

    A container's duration spans every track it holds, so it can run past the
    end of the video by a second or more, and multiplying it by the frame rate
    then asks the sampler for frames that do not exist. The video track's own
    duration is preferred wherever the metadata reported one.

    Args:
        fps: Frames per second.
        track_duration: Video track duration in seconds, if known.
        container_duration: Container duration in seconds.

    Returns:
        A frame count, or None if neither the rate nor any duration is usable.
    """
    duration = track_duration if track_duration is not None else container_duration
    if fps <= 0 or duration <= 0:
        return None
    return int(fps * duration)


# Mapping of ffprobe chroma_location to x265 chromaloc integers
CHROMALOC_MAP: dict[str, int] = {
    "left": 0,
    "unspecified": 0,
    "center": 1,
    "topleft": 2,
    "top": 3,
    "bottomleft": 4,
    "bottom": 5,
}


def parse_video_info(
    input_path: Path, ffprobe_bin: str = "ffprobe", log_hdr_metadata: bool = True
) -> VideoInfo:
    # First check if the file has a video stream
    check_cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=nw=1:nk=1",
        str(input_path),
    ]
    check_proc = subprocess.run(check_cmd, capture_output=True, text=True)

    # Check for common error patterns indicating invalid video file
    stderr_lower = check_proc.stderr.lower()
    stdout_lower = check_proc.stdout.lower()

    if check_proc.returncode != 0 or not check_proc.stdout.strip():
        # Detect common error patterns
        if (
            "invalid data found when processing input" in stderr_lower
            or "invalid data found when processing input" in stdout_lower
        ):
            raise InvalidVideoFileError(
                f"The input file is not a valid video file: {input_path}\n"
                + "Please provide a video file (e.g., .mkv, .mp4, .avi)"
            )
        elif "no such file or directory" in stderr_lower:
            raise InvalidVideoFileError(f"Input file not found: {input_path}")
        else:
            # Generic error for files without video streams
            raise InvalidVideoFileError(
                f"No video stream found in input file: {input_path}\n"
                + f"ffprobe output: {check_proc.stderr or check_proc.stdout}"
            )

    if not check_proc.stdout.strip().startswith("video"):
        raise InvalidVideoFileError(
            f"The input file does not contain a video stream: {input_path}"
        )

    # Query all stream and format properties in a single ffprobe call
    stream, fmt = _run_ffprobe_json(
        input_path,
        stream_entries=[
            "avg_frame_rate",
            "r_frame_rate",
            "pix_fmt",
            "width",
            "height",
            "color_space",
            "color_transfer",
            "color_primaries",
            "color_range",
            "chroma_location",
        ],
        format_entries=["duration"],
        ffprobe_bin=ffprobe_bin,
    )

    # Parse frame rate (try avg_frame_rate first, fall back to r_frame_rate)
    avg_frame_rate = get_str(stream, "avg_frame_rate") or "0/0"
    r_frame_rate = get_str(stream, "r_frame_rate") or "0/0"
    fps = parse_fraction(avg_frame_rate)
    if fps <= 0.0:
        fps = parse_fraction(r_frame_rate)

    # Parse duration
    duration = get_float(fmt, "duration")
    if duration is None:
        raise RuntimeError(f"ffprobe failed to get duration for: {input_path}")

    # Parse video properties
    pix_fmt = get_str(stream, "pix_fmt")
    width = get_int(stream, "width")
    height = get_int(stream, "height")
    color_space = get_str(stream, "color_space")
    color_trc = get_str(stream, "color_transfer")
    color_primaries = get_str(stream, "color_primaries")
    color_range = get_str(stream, "color_range")

    # Parse chroma location
    chroma_loc_str = get_str(stream, "chroma_location")
    chroma_loc: int | None = None
    if chroma_loc_str:
        chroma_loc = CHROMALOC_MAP.get(chroma_loc_str.lower())
        if chroma_loc is not None:
            logger.debug(
                "Detected chromaloc from %s: %s -> %s",
                input_path.name,
                chroma_loc_str,
                chroma_loc,
            )
        else:
            logger.warning(
                "Unknown chroma_location '%s' from %s", chroma_loc_str, input_path.name
            )

    # Extract HDR metadata, bitrate, and frame count using pymediainfo
    mastering_display_primaries: str | None = None
    mastering_display_luminance: str | None = None
    max_cll: str | None = None
    max_fall: str | None = None
    video_bitrate_kbps: float | None = None
    frame_count: int | None = None
    track_duration: float | None = None

    try:
        from pymediainfo import MediaInfo

        media_info = MediaInfo.parse(str(input_path))
        if media_info.video_tracks:
            video_track = media_info.video_tracks[0]

            # Reported in milliseconds, and only used to estimate a frame count
            # when the track carries none of its own.
            raw_track_duration = cast(float | int | str | None, video_track.duration)
            if raw_track_duration is not None:
                with suppress(ValueError, TypeError):
                    track_duration = float(raw_track_duration) / 1000.0

            mastering_display_primaries = cast(
                str | None, video_track.mastering_display_color_primaries
            )
            mastering_display_luminance = cast(
                str | None, video_track.mastering_display_luminance
            )
            max_cll = cast(str | None, video_track.maximum_content_light_level)
            max_fall = cast(str | None, video_track.maximum_frameaverage_light_level)

            # Extract frame count from container metadata
            frame_count_raw = cast(int | str | None, video_track.frame_count)
            if frame_count_raw is not None:
                with suppress(ValueError, TypeError):
                    frame_count = int(frame_count_raw)
                    if frame_count <= 0:
                        frame_count = None

            # Extract video stream bitrate (in bits per second from pymediainfo)
            # First try video track bit_rate, then fall back to overall_bit_rate
            raw_bitrate = cast(
                float | int | str | None, getattr(video_track, "bit_rate", None)
            )
            if raw_bitrate is not None:
                try:
                    # pymediainfo returns bitrate in bits per second
                    video_bitrate_kbps = float(raw_bitrate) / 1000.0
                    logger.debug(
                        "Video bitrate from pymediainfo video track: %.0f kbps",
                        video_bitrate_kbps,
                    )
                except (ValueError, TypeError):
                    logger.warning("Failed to parse video bitrate: %s", raw_bitrate)

            # Fallback to overall bitrate from general track if video bitrate not available  # noqa: E501  # TODO(E501): shorten line
            if video_bitrate_kbps is None and media_info.general_tracks:
                general_track = media_info.general_tracks[0]
                overall_bitrate = cast(
                    float | int | str | None,
                    getattr(general_track, "overall_bit_rate", None),
                )
                if overall_bitrate is not None:
                    try:
                        video_bitrate_kbps = float(overall_bitrate) / 1000.0
                        logger.debug(
                            "Video bitrate from pymediainfo overall: %.0f kbps (includes audio)",  # noqa: E501  # TODO(E501): shorten line
                            video_bitrate_kbps,
                        )
                    except (ValueError, TypeError):
                        logger.warning(
                            "Failed to parse overall bitrate: %s", overall_bitrate
                        )

            logger.debug(
                "HDR metadata from pymediainfo: primaries=%s, luminance=%s, MaxCLL=%s, MaxFALL=%s",  # noqa: E501  # TODO(E501): shorten line
                mastering_display_primaries,
                mastering_display_luminance,
                max_cll,
                max_fall,
            )

            # Log HDR metadata summary once at INFO level for user visibility
            # Only log for source video (log_hdr_metadata=True), not for encoded files
            if log_hdr_metadata and (mastering_display_primaries or max_cll):
                hdr_parts: list[str] = []
                if mastering_display_primaries:
                    hdr_parts.append(f"colorspace={mastering_display_primaries}")
                if max_cll:
                    hdr_parts.append(f"MaxCLL={max_cll}")
                if max_fall:
                    hdr_parts.append(f"MaxFALL={max_fall}")
                logger.info("HDR metadata: %s", ", ".join(hdr_parts))
    except ImportError:
        logger.warning(
            "pymediainfo not available, HDR metadata and video bitrate will not be extracted"  # noqa: E501  # TODO(E501): shorten line
        )

    # Fallback: estimate from the frame rate when no track reported a count
    if frame_count is None:
        frame_count = estimate_frame_count(
            fps, track_duration=track_duration, container_duration=duration
        )

    return VideoInfo(
        fps=fps,
        duration=duration,
        pix_fmt=pix_fmt or None,
        width=width,
        height=height,
        color_primaries=color_primaries,
        color_trc=color_trc,
        color_space=color_space,
        color_range=color_range,
        chroma_location=chroma_loc,
        video_bitrate_kbps=video_bitrate_kbps,
        frame_count=frame_count,
        mastering_display_color_primaries=mastering_display_primaries,
        mastering_display_luminance=mastering_display_luminance,
        maximum_content_light_level=max_cll,
        maximum_frameaverage_light_level=max_fall,
    )


def get_frame_count(path: Path, info: VideoInfo | None = None) -> int:
    """Get frame count from VideoInfo.

    Args:
        path: Path to the video file (unused, kept for API compatibility)
        info: VideoInfo from parse_video_info() - contains frame_count

    Returns:
        Frame count (minimum 1)
    """
    _ = path  # Unused, kept for API compatibility

    if info and info.frame_count and info.frame_count > 0:
        return info.frame_count

    return 1


def get_assessment_frame_count(
    video_path: Path,
    ffprobe_bin: str = "ffprobe",
) -> int:
    """Get frame count for assessment progress tracking.

    Convenience wrapper for assessment modules that need frame counts for
    progress bars. Suppresses HDR metadata logging since assessments
    typically process already-encoded files, not source videos.

    Args:
        video_path: Path to video file
        ffprobe_bin: Path to ffprobe binary

    Returns:
        Frame count (minimum 1)
    """
    info = parse_video_info(video_path, ffprobe_bin=ffprobe_bin, log_hdr_metadata=False)
    return get_frame_count(video_path, info)


SUPPORTED_BIT_DEPTHS: frozenset[int] = frozenset({8, 10, 12, 14, 16})

# Semi-planar formats put the chroma layout first and the depth second, so
# p010le is 4:2:0 10-bit and p210le is 4:2:2 10-bit. Reading all three digits
# as one number gives 210, which is why this has to be tried before the planar
# pattern below.
_SEMI_PLANAR_RE = re.compile(r"^p\d(\d{2})")

# Planar formats put the depth after the plane marker: yuv420p10le, gbrp10le.
_PLANAR_RE = re.compile(r"p(\d+)")

# Packed RGB names the total across every component: rgb48le is 3 components of
# 16 bits, bgra64le is 4 of 16. The component letters have to be followed
# immediately by digits, which is what keeps this off planar gbrp10le.
_PACKED_RGB_RE = re.compile(r"^(?P<comps>[abgr]+)(?P<bits>\d+)")


def get_bit_depth_from_pix_fmt(pix_fmt: str | None) -> int:
    """Extract the per-component bit depth from an FFmpeg pixel format name.

    Args:
        pix_fmt: Pixel format string from FFmpeg (e.g., 'yuv420p10le', 'yuv420p')

    Returns:
        Bit depth as integer (8, 10, 12, 14, or 16), defaults to 8 if unable to
        parse or if the depth is one no encoder here accepts

    Examples:
        >>> get_bit_depth_from_pix_fmt('yuv420p10le')
        10
        >>> get_bit_depth_from_pix_fmt('p210le')
        10
        >>> get_bit_depth_from_pix_fmt('rgb48le')
        16
        >>> get_bit_depth_from_pix_fmt('yuv420p')
        8
    """
    if not pix_fmt:
        return 8

    depth: int | None = None

    semi_planar = _SEMI_PLANAR_RE.match(pix_fmt)
    if semi_planar:
        depth = int(semi_planar.group(1))
    else:
        planar = _PLANAR_RE.search(pix_fmt)
        if planar:
            depth = int(planar.group(1))
        else:
            packed = _PACKED_RGB_RE.match(pix_fmt)
            if packed:
                components = len(packed.group("comps"))
                total_bits = int(packed.group("bits"))
                depth = total_bits // components

    if depth in SUPPORTED_BIT_DEPTHS:
        return depth

    # Unparseable, or a depth no encoder here accepts (9-bit, for instance)
    return 8


class VideoFormat(StrEnum):
    """Video format classification based on dynamic range"""

    SDR = "SDR"
    HDR = "HDR"


def get_video_format(video_info: VideoInfo) -> VideoFormat:
    """Classify a source as HDR or SDR from its transfer characteristic.

    Args:
        video_info: Parsed metadata for the source

    Returns:
        VideoFormat.HDR for a PQ or HLG transfer, otherwise VideoFormat.SDR
    """
    return VideoFormat.HDR if is_hdr_video(video_info.color_trc) else VideoFormat.SDR


@dataclass(frozen=True)
class EncodeStats:
    """Statistics for an encoded video file."""

    file_size_bytes: int
    duration_seconds: float
    bitrate_kbps: float


def get_encode_stats(
    file_path: Path, ffprobe_bin: str = "ffprobe"
) -> EncodeStats | None:
    """Get file size, duration, and bitrate for an encoded video file.

    Args:
        file_path: Path to the encoded video file
        ffprobe_bin: Path to ffprobe binary (default: "ffprobe")

    Returns:
        EncodeStats with file size, duration, and bitrate, or None if failed
    """
    import os

    if not file_path.exists():
        logger.warning("File not found for encode stats: %s", file_path)
        return None

    # Get file size
    file_size_bytes = os.path.getsize(file_path)

    # Get duration from ffprobe
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            logger.warning("ffprobe failed for encode stats: %s", proc.stderr)
            return None

        duration_seconds = float(proc.stdout.strip())

        # Calculate bitrate (kbps)
        if duration_seconds > 0:
            bitrate_kbps = (file_size_bytes * 8) / (duration_seconds * 1000)
        else:
            bitrate_kbps = 0.0

        return EncodeStats(
            file_size_bytes=file_size_bytes,
            duration_seconds=duration_seconds,
            bitrate_kbps=bitrate_kbps,
        )

    except Exception as e:
        logger.warning("Failed to get encode stats for %s: %s", file_path, e)
        return None
