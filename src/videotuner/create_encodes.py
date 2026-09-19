from __future__ import annotations

import logging
import shlex
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .media import VideoInfo
    from .profiles import Profile

from .encoding_utils import (
    CropValues,
    EncoderPaths,
    IndexerType,
    SampledSource,
    build_encoder_command,
    build_sampling_vpy_script,
    build_vspipe_command,
    calculate_sample_count,
    create_temp_encode_paths,
    mux_and_cleanup,
    resolve_absolute_path,
    run_vspipe_encode,
    write_vpy_script,
)
from .media import get_video_format
from .profiles import Profile, create_multipass_profile
from .utils import log_separator, run_capture

logger = logging.getLogger(__name__)


def mux_to_mkv(
    bitstream_path: Path,
    output_path: Path,
    *,
    mkvmerge_bin: str = "mkvmerge",
    cwd: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
) -> None:
    """Mux a raw bitstream (HEVC or H.264) into an MKV container via mkvmerge."""
    log = logging.getLogger(__name__)

    if not bitstream_path.exists():
        raise FileNotFoundError(f"Bitstream not found: {bitstream_path}")

    mux_args = [
        mkvmerge_bin,
        "--gui-mode",
        "--stop-after-video-ends",
        "-o",
        str(output_path),
        str(bitstream_path),
    ]

    log.debug("mkvmerge mux cmd: %s", " ".join(shlex.quote(str(c)) for c in mux_args))
    _ = run_capture(mux_args, cwd=cwd, line_callback=line_handler)


def encode_concatenated_reference(
    sources: Sequence[SampledSource],
    output_path: Path,
    interval_frames: int,
    region_frames: int,
    fps: float,
    profile: Profile,
    video_info: VideoInfo,
    *,
    indexer: IndexerType,
    mkvmerge_bin: str = "mkvmerge",
    cwd: Path | None = None,
    temp_dir: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
    mux_handler: Callable[[str], bool] | None = None,
    perform_mux: bool = True,
    enable_cropdetect: bool = False,
    crop_values: CropValues | None = None,
    metric_label: str | None = None,
) -> Path:
    """
    Encode concatenated lossless reference using periodic sampling.

    Uses VapourSynth's SelectEvery to efficiently sample frames at regular
    intervals, avoiding manual region extraction and splicing.

    Args:
        sources: Files to sample, each carrying its own usable range. Guard
            bands are a fraction of a file's length, so ranges differ per file
            and are settled by the caller.
        output_path: Output MKV path
        interval_frames: Sample every N frames
        region_frames: Number of consecutive frames per sample
        fps: Video framerate
        profile: Encoding profile (should use preset="ultrafast")
        video_info: MediaInfo from ffprobe
        indexer: Source filter to load each file with.
        mkvmerge_bin: Path to mkvmerge binary
        cwd: Working directory
        temp_dir: Directory for temporary files
        line_handler: Optional callback for encoder progress
        mux_handler: Optional callback for mkvmerge progress
        enable_cropdetect: Whether to apply cropdetect
        crop_values: Pre-calculated crop values
        metric_label: Optional label for log messages (e.g., "VMAF", "SSIM2")

    Returns:
        Path to the produced file (MKV if muxed, bitstream if mux is deferred).

    Raises:
        ValueError: If parameters are invalid
        FileNotFoundError: If required binaries not found
    """
    log = logging.getLogger(__name__)
    encoder_type = profile.encoder

    # Resolve and validate encoder paths
    paths = EncoderPaths.from_cwd(cwd, encoder_type)
    paths.validate()

    # Determine video format and build encoder params (lossless)
    video_format = get_video_format(video_info)
    encoder_params = profile.to_encoder_params(
        crf=0.0,
        video_format=video_format,
        video_info=video_info,
        is_lossless=True,
    )

    # Create temp files
    vpy_path, bitstream_path = create_temp_encode_paths(
        encoder_type=encoder_type, temp_dir=temp_dir, name="concatenated_reference"
    )

    # Build and write VapourSynth script
    effective_crop = crop_values if enable_cropdetect else None
    vpy_content = build_sampling_vpy_script(
        sources=sources,
        interval_frames=interval_frames,
        region_frames=region_frames,
        fps=fps,
        cwd=cwd,
        crop_values=effective_crop,
        indexer=indexer,
    )

    write_vpy_script(vpy_path, vpy_content)

    # Log encoding info
    usable_frames = sum(s.usable_range.frame_count for s in sources)
    num_samples, total_sampled_frames = calculate_sample_count(
        usable_frames, interval_frames, region_frames
    )
    label_prefix = f"{metric_label} " if metric_label else ""
    log.info(
        "Encoding %slossless reference: %d samples, %d total frames (interval=%d, region=%d)",  # noqa: E501  # TODO(E501): shorten line
        label_prefix,
        num_samples,
        total_sampled_frames,
        interval_frames,
        region_frames,
    )
    log.debug("VapourSynth script:\n%s", vpy_content)

    # Build and run vspipe | encoder pipeline
    vspipe_args = build_vspipe_command(vs_env=paths.vs_env, vpy_path=vpy_path, cwd=cwd)
    encoder_args = build_encoder_command(
        paths=paths,
        output_path=bitstream_path,
        encoder_params=encoder_params,
        preset=profile.preset,
        cwd=cwd,
    )

    env = paths.vs_env.build_env()
    _ = run_vspipe_encode(vspipe_args, encoder_args, env, cwd, line_handler)

    # Mux and cleanup
    final_output = mux_and_cleanup(
        bitstream_path=bitstream_path,
        output_path=output_path,
        vpy_path=vpy_path,
        perform_mux=perform_mux,
        mux_fn=mux_to_mkv,
        mkvmerge_bin=mkvmerge_bin,
        cwd=cwd,
        mux_handler=mux_handler,
    )

    if perform_mux:
        log.info("%slossless reference created: %s", label_prefix, output_path.name)
    else:
        log.info(
            "%slossless reference encoded (mux deferred): %s",
            label_prefix,
            bitstream_path.name,
        )

    return final_output


def encode_concatenated_distorted(
    sources: Sequence[SampledSource],
    output_path: Path,
    interval_frames: int,
    region_frames: int,
    fps: float,
    profile: Profile,
    crf: float,
    video_info: VideoInfo,
    *,
    indexer: IndexerType,
    mkvmerge_bin: str = "mkvmerge",
    cwd: Path | None = None,
    temp_dir: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
    mux_handler: Callable[[str], bool] | None = None,
    perform_mux: bool = True,
    enable_cropdetect: bool = False,
    crop_values: CropValues | None = None,
    metric_label: str | None = None,
) -> Path:
    """
    Encode concatenated distorted clip using periodic sampling with CRF.

    Uses VapourSynth's SelectEvery to efficiently sample frames at regular
    intervals, then encodes with specified CRF value.

    Args:
        sources: Files to sample, each carrying its own usable range. Guard
            bands are a fraction of a file's length, so ranges differ per file
            and are settled by the caller.
        output_path: Output MKV path
        interval_frames: Sample every N frames
        region_frames: Number of consecutive frames per sample
        fps: Video framerate
        profile: Encoding profile
        crf: CRF value for encoding
        video_info: MediaInfo from ffprobe
        indexer: Source filter to load each file with.
        mkvmerge_bin: Path to mkvmerge binary
        cwd: Working directory
        temp_dir: Directory for temporary files
        line_handler: Optional callback for encoder progress
        mux_handler: Optional callback for mkvmerge progress
        enable_cropdetect: Whether to apply cropdetect
        crop_values: Pre-calculated crop values
        metric_label: Optional label for log messages (e.g., "VMAF", "SSIM2")

    Returns:
        Path to the produced file (MKV if muxed, bitstream if mux is deferred).

    Raises:
        ValueError: If parameters are invalid
        FileNotFoundError: If required binaries not found
    """
    log = logging.getLogger(__name__)
    encoder_type = profile.encoder

    # Resolve and validate encoder paths
    paths = EncoderPaths.from_cwd(cwd, encoder_type)
    paths.validate()

    # Determine video format and build encoder params (CRF encoding)
    video_format = get_video_format(video_info)
    encoder_params = profile.to_encoder_params(
        crf=crf,
        video_format=video_format,
        video_info=video_info,
        is_lossless=False,
    )

    # Create temp files
    vpy_path, bitstream_path = create_temp_encode_paths(
        encoder_type=encoder_type,
        temp_dir=temp_dir,
        name=f"concatenated_distorted_crf{crf}",
    )

    # Build and write VapourSynth script
    effective_crop = crop_values if enable_cropdetect else None
    vpy_content = build_sampling_vpy_script(
        sources=sources,
        interval_frames=interval_frames,
        region_frames=region_frames,
        fps=fps,
        cwd=cwd,
        crop_values=effective_crop,
        indexer=indexer,
    )

    write_vpy_script(vpy_path, vpy_content)

    # Log encoding info
    usable_frames = sum(s.usable_range.frame_count for s in sources)
    num_samples, total_sampled_frames = calculate_sample_count(
        usable_frames, interval_frames, region_frames
    )
    label_prefix = f"{metric_label} " if metric_label else ""
    log.info(
        "Encoding %sdistorted clip (CRF %.1f): %d samples, %d total frames (interval=%d, region=%d)",  # noqa: E501  # TODO(E501): shorten line
        label_prefix,
        crf,
        num_samples,
        total_sampled_frames,
        interval_frames,
        region_frames,
    )
    log.debug("VapourSynth script:\n%s", vpy_content)

    # Build and run vspipe | encoder pipeline
    vspipe_args = build_vspipe_command(vs_env=paths.vs_env, vpy_path=vpy_path, cwd=cwd)
    encoder_args = build_encoder_command(
        paths=paths,
        output_path=bitstream_path,
        encoder_params=encoder_params,
        preset=profile.preset,
        cwd=cwd,
    )

    env = paths.vs_env.build_env()
    _ = run_vspipe_encode(vspipe_args, encoder_args, env, cwd, line_handler)

    # Mux and cleanup
    final_output = mux_and_cleanup(
        bitstream_path=bitstream_path,
        output_path=output_path,
        vpy_path=vpy_path,
        perform_mux=perform_mux,
        mux_fn=mux_to_mkv,
        mkvmerge_bin=mkvmerge_bin,
        cwd=cwd,
        mux_handler=mux_handler,
    )

    if perform_mux:
        log.info("%sdistorted clip created: %s", label_prefix, output_path.name)
    else:
        log.info(
            "%sdistorted bitstream encoded (mux deferred): %s",
            label_prefix,
            bitstream_path.name,
        )

    return final_output


def encode_concatenated_bitrate(
    sources: Sequence[SampledSource],
    output_path: Path,
    interval_frames: int,
    region_frames: int,
    fps: float,
    profile: Profile,
    video_info: VideoInfo,
    *,
    indexer: IndexerType,
    mkvmerge_bin: str = "mkvmerge",
    cwd: Path | None = None,
    temp_dir: Path | None = None,
    stats_file: Path | None = None,
    analysis_file: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
    mux_handler: Callable[[str], bool] | None = None,
    perform_mux: bool = True,
    enable_cropdetect: bool = False,
    crop_values: CropValues | None = None,
    metric_label: str | None = None,
) -> Path:
    """
    Encode concatenated clip using bitrate mode with optional multi-pass support.

    Similar to encode_concatenated_distorted but uses bitrate mode instead of CRF.
    Supports single-pass (pass=1 or no pass) and multi-pass (pass=2/3) encoding.

    Args:
        source_path: Input source video path
        output_path: Output MKV path
        interval_frames: Sample every N frames
        region_frames: Number of consecutive frames per sample
        guard_start_frames: Frames to skip at start
        guard_end_frames: Frames to skip at end
        total_frames: Total frames in source video
        fps: Video framerate
        profile: Encoding profile (must have bitrate set)
        video_info: MediaInfo from ffprobe
        indexer: Source filter to load each file with.
        mkvmerge_bin: Path to mkvmerge binary
        cwd: Working directory
        temp_dir: Directory for temporary files
        stats_file: Path to stats file (for pass 2/3, or output location for pass 1)
        analysis_file: Path to analysis file for multi-pass optimization (optional)
        line_handler: Optional callback for encoder progress
        mux_handler: Optional callback for mkvmerge progress
        perform_mux: Whether to mux bitstream to MKV
        enable_cropdetect: Whether to apply cropdetect
        crop_values: Pre-calculated crop values
        metric_label: Optional label for log messages (e.g., "VMAF", "SSIM2")

    Returns:
        Path to the produced file (MKV if muxed, bitstream if mux is deferred)

    Raises:
        ValueError: If profile is not in bitrate mode or parameters are invalid
        FileNotFoundError: If required binaries not found
    """
    log = logging.getLogger(__name__)
    encoder_type = profile.encoder

    # Validate profile is in bitrate mode
    if not profile.is_bitrate_mode:
        raise ValueError(
            f"Profile '{profile.name}' is not in bitrate mode. "
            + "Use encode_concatenated_distorted for CRF encoding."
        )

    pass_num = profile.pass_number or 1

    # Validate stats file requirements
    if pass_num in (2, 3) and stats_file is None:
        raise ValueError(f"Pass {pass_num} requires stats_file parameter")

    # Resolve and validate encoder paths
    paths = EncoderPaths.from_cwd(cwd, encoder_type)
    paths.validate()

    # Determine video format
    video_format = get_video_format(video_info)

    log_separator(log)
    log.info("Profile configuration:")
    log.info("  Name: %s", profile.name)
    log.info("  Encoder: %s", encoder_type.value)
    log.info("  Preset: %s", profile.preset)
    log.info("  Bitrate mode: %s", profile.is_bitrate_mode)
    log.info("  Bitrate: %s kbps", profile.bitrate)
    log.info("  Pass number: %s", profile.pass_number)
    log.info("  Settings: %s", profile.settings)
    log_separator(log)

    # Build encoder params (bitrate encoding with stats file and optional analysis file)
    enc_params = profile.to_encoder_params(
        crf=0.0,  # Ignored in bitrate mode
        video_format=video_format,
        video_info=video_info,
        is_lossless=False,
        stats_file=stats_file,
        analysis_file=analysis_file,
    )

    bitrate_kbps = profile.bitrate or 0

    log_separator(log)
    log.info("Generated %s parameters:", encoder_type.value)
    log.info("  %s", " ".join(enc_params))
    log_separator(log)

    # Create temp files
    vpy_path, bitstream_path = create_temp_encode_paths(
        encoder_type=encoder_type,
        temp_dir=temp_dir,
        name=f"concatenated_bitrate{bitrate_kbps}_pass{pass_num}",
    )

    # Build and write VapourSynth script
    effective_crop = crop_values if enable_cropdetect else None
    vpy_content = build_sampling_vpy_script(
        sources=sources,
        interval_frames=interval_frames,
        region_frames=region_frames,
        fps=fps,
        cwd=cwd,
        crop_values=effective_crop,
        indexer=indexer,
    )

    write_vpy_script(vpy_path, vpy_content)

    # Log encoding info
    usable_frames = sum(s.usable_range.frame_count for s in sources)
    num_samples, total_sampled_frames = calculate_sample_count(
        usable_frames, interval_frames, region_frames
    )
    label_prefix = f"{metric_label} " if metric_label else ""
    log.info(
        "Encoding %sbitrate clip (pass %d, %d kbps): %d samples, %d total frames",
        label_prefix,
        pass_num,
        bitrate_kbps,
        num_samples,
        total_sampled_frames,
    )

    log_separator(log)
    log.info("VapourSynth script content:")
    log_separator(log)
    log.info("%s", vpy_content)
    log_separator(log)

    # Build vspipe | encoder pipeline
    vspipe_args = build_vspipe_command(vs_env=paths.vs_env, vpy_path=vpy_path, cwd=cwd)
    encoder_args = build_encoder_command(
        paths=paths,
        output_path=bitstream_path,
        encoder_params=enc_params,
        preset=profile.preset,
        cwd=cwd,
    )

    # Detailed execution logging
    abs_vpy_path = resolve_absolute_path(vpy_path, cwd)
    abs_bitstream_path = resolve_absolute_path(bitstream_path, cwd)

    log_separator(log)
    log.info("Encoding Details:")
    log.info(
        "  %s binary: %s (exists: %s)",
        encoder_type.value,
        paths.encoder_bin,
        paths.encoder_bin.exists(),
    )
    log.info(
        "  vspipe binary: %s (exists: %s)",
        paths.vs_env.vspipe_bin,
        paths.vs_env.vspipe_bin.exists(),
    )
    log.info(
        "  FFMS2 DLL: %s (exists: %s)",
        paths.vs_env.ffms2_dll,
        paths.vs_env.ffms2_dll.exists(),
    )
    log.info("  VPY script: %s (exists: %s)", abs_vpy_path, abs_vpy_path.exists())
    log.info("  Output bitstream: %s", abs_bitstream_path)
    if stats_file:
        log.info("  Stats file: %s", stats_file)
    log.info("  Working directory: %s", cwd)
    log_separator(log)
    log.info("Full vspipe command:")
    log.info("  %s", " ".join(shlex.quote(str(c)) for c in vspipe_args))
    log.info("Full %s command:", encoder_type.value)
    log.info("  %s", " ".join(shlex.quote(str(c)) for c in encoder_args))
    log_separator(log)

    # Setup VapourSynth environment
    env = paths.vs_env.build_env()

    log.info("Starting %s encoding...", encoder_type.value)

    try:
        # Run vspipe | encoder pipeline and capture output for debugging
        try:
            output = run_vspipe_encode(
                vspipe_args, encoder_args, env, cwd, line_handler
            )
            log_separator(log)
            log.info("%s output (last 50 lines):", encoder_type.value)
            log_separator(log)
            output_lines = output.strip().split("\n")
            for line in output_lines[-50:]:
                log.info("  %s", line)
            log_separator(log)
        except Exception as e:
            log_separator(log, logging.ERROR)
            log.error("%s encoding FAILED!", encoder_type.value)
            log_separator(log, logging.ERROR)
            log.error("Error: %s", str(e))
            log_separator(log, logging.ERROR)
            raise

        final_output: Path
        if perform_mux:
            log.info("Muxing bitstream -> MKV")
            mux_to_mkv(
                bitstream_path=bitstream_path,
                output_path=output_path,
                mkvmerge_bin=mkvmerge_bin,
                cwd=cwd,
                line_handler=mux_handler,
            )
            final_output = output_path
            log.info("%sbitrate clip created: %s", label_prefix, output_path.name)
        else:
            final_output = bitstream_path
            log.info(
                "%sbitrate bitstream encoded (mux deferred): %s",
                label_prefix,
                bitstream_path.name,
            )

    finally:
        if perform_mux and bitstream_path.exists():
            bitstream_path.unlink()
        if vpy_path.exists():
            vpy_path.unlink()

    return final_output


def encode_multipass_bitrate(
    sources: Sequence[SampledSource],
    output_path: Path,
    interval_frames: int,
    region_frames: int,
    fps: float,
    profile: Profile,
    video_info: VideoInfo,
    *,
    indexer: IndexerType,
    mkvmerge_bin: str = "mkvmerge",
    cwd: Path | None = None,
    temp_dir: Path | None = None,
    stats_file: Path,
    line_handler: Callable[[str], bool] | None = None,
    mux_handler: Callable[[str], bool] | None = None,
    enable_cropdetect: bool = False,
    crop_values: CropValues | None = None,
) -> Path:
    """
    Encode concatenated clip using multi-pass bitrate encoding.

    Automatically handles 2-pass or 3-pass encoding based on profile settings:
    - 2-pass mode (pass=2): Pass 1 → Pass 2
    - 3-pass mode (pass=3): Pass 1 → Pass 3 → Pass 2 (three sequential passes)

    Pass sequence:
    - Pass 1: Analyzes video and creates initial stats file
    - Pass 3: Refines stats file using initial analysis (intermediate pass, 3-pass only)
    - Pass 2: Creates final encode using refined stats file (final pass)

    Args:
        source_path: Input source video path
        output_path: Output MKV path
        interval_frames: Sample every N frames
        region_frames: Number of consecutive frames per sample
        guard_start_frames: Frames to skip at start
        guard_end_frames: Frames to skip at end
        total_frames: Total frames in source video
        fps: Video framerate
        profile: Encoding profile (must have pass=2 or pass=3)
        video_info: MediaInfo from ffprobe
        indexer: Source filter to load each file with.
        mkvmerge_bin: Path to mkvmerge binary
        cwd: Working directory
        temp_dir: Directory for temporary files
        stats_file: Path for stats file (shared across all passes)
        line_handler: Optional callback for x265 progress
        mux_handler: Optional callback for mkvmerge progress
        enable_cropdetect: Whether to apply cropdetect
        crop_values: Pre-calculated crop values

    Returns:
        Path to final output file

    Raises:
        ValueError: If profile doesn't use multi-pass (pass=2 or pass=3)
    """
    log = logging.getLogger(__name__)

    pass_num = profile.pass_number
    if pass_num not in (2, 3):
        raise ValueError(
            f"Profile '{profile.name}' must specify pass=2 or pass=3 for multi-pass encoding"  # noqa: E501  # TODO(E501): shorten line
        )

    # Create a modified profile for pass 1
    pass1_profile = create_multipass_profile(profile, 1)

    # Determine pass sequence based on final pass number
    if pass_num == 3:
        log.info("Starting 3-pass bitrate encoding (Pass 1 -> Pass 3 -> Pass 2)")
    else:
        log.info("Starting 2-pass bitrate encoding (Pass 1 -> Pass 2)")

    # Pass 1: Create stats file
    log.info("=== Pass 1: Analyzing video and creating stats file ===")
    if temp_dir:
        pass1_output = temp_dir / f"pass1_{output_path.name}"
    else:
        pass1_output = output_path.parent / f"pass1_{output_path.name}"

    _ = encode_concatenated_bitrate(
        sources=sources,
        output_path=pass1_output,
        interval_frames=interval_frames,
        region_frames=region_frames,
        fps=fps,
        profile=pass1_profile,
        video_info=video_info,
        indexer=indexer,
        mkvmerge_bin=mkvmerge_bin,
        cwd=cwd,
        temp_dir=temp_dir,
        stats_file=stats_file,
        line_handler=line_handler,
        mux_handler=None,  # Don't mux pass 1 output
        perform_mux=False,
        enable_cropdetect=enable_cropdetect,
        crop_values=crop_values,
    )

    # Clean up pass 1 output (we only need stats file)
    if pass1_output.exists():
        try:
            pass1_output.unlink()
            log.debug("Cleaned up pass 1 output: %s", pass1_output)
        except Exception as e:
            log.warning("Failed to clean up pass 1 output: %s", e)

    # For 3-pass: Run Pass 3 (intermediate) before Pass 2 (final)
    if pass_num == 3:
        log.info("=== Pass 3: Refining stats file (intermediate pass) ===")

        # Create a modified profile for pass 3
        pass3_profile = create_multipass_profile(profile, 3)

        if temp_dir:
            pass3_output = temp_dir / f"pass3_{output_path.name}"
        else:
            pass3_output = output_path.parent / f"pass3_{output_path.name}"

        _ = encode_concatenated_bitrate(
            sources=sources,
            output_path=pass3_output,
            interval_frames=interval_frames,
            region_frames=region_frames,
            fps=fps,
            profile=pass3_profile,
            video_info=video_info,
            indexer=indexer,
            mkvmerge_bin=mkvmerge_bin,
            cwd=cwd,
            temp_dir=temp_dir,
            stats_file=stats_file,
            line_handler=line_handler,
            mux_handler=None,  # Don't mux pass 3 output
            perform_mux=False,
            enable_cropdetect=enable_cropdetect,
            crop_values=crop_values,
        )

        # Clean up pass 3 output (we only need stats file)
        if pass3_output.exists():
            try:
                pass3_output.unlink()
                log.debug("Cleaned up pass 3 output: %s", pass3_output)
            except Exception as e:
                log.warning("Failed to clean up pass 3 output: %s", e)

    # Final pass: Pass 2 (always the final encoding pass)
    # For 2-pass mode: this runs after Pass 1
    # For 3-pass mode: this runs after Pass 1 → Pass 3
    log.info("=== Pass 2: Final encode using refined stats file ===")

    # Create a modified profile for final pass 2
    pass2_profile = create_multipass_profile(profile, 2)

    final_output = encode_concatenated_bitrate(
        sources=sources,
        output_path=output_path,
        interval_frames=interval_frames,
        region_frames=region_frames,
        fps=fps,
        profile=pass2_profile,  # Always use pass 2 for final encode
        video_info=video_info,
        indexer=indexer,
        mkvmerge_bin=mkvmerge_bin,
        cwd=cwd,
        temp_dir=temp_dir,
        stats_file=stats_file,
        line_handler=line_handler,
        mux_handler=mux_handler,
        perform_mux=True,
        enable_cropdetect=enable_cropdetect,
        crop_values=crop_values,
    )

    log.info("Multi-pass bitrate encoding complete")
    return final_output
