"""Reference generation utilities for the encoding pipeline.

This module contains helpers for generating lossless reference encodes
used for quality assessment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .encoding_utils import SampledSource

if TYPE_CHECKING:
    from .encoding_utils import CropValues
    from .media import VideoInfo
    from .pipeline_cli import PipelineArgs
    from .profiles import Profile
    from .progress import PipelineDisplay


def are_sampling_params_equal(args: PipelineArgs) -> bool:
    """Check if VMAF and SSIM2 sampling parameters are identical.

    Returns True if both metrics are enabled AND their interval_frames
    and region_frames match, enabling shared sample generation.

    Args:
        args: Pipeline arguments containing sampling configuration

    Returns:
        True if samples can be shared between metrics
    """
    return (
        args.vmaf
        and args.ssim2
        and args.vmaf_interval_frames == args.ssim2_interval_frames
        and args.vmaf_region_frames == args.ssim2_region_frames
    )


@dataclass(frozen=True)
class MetricSamplingParams:
    """How a metric samples the source a job reads.

    The source may be several files, each with its own usable range: guard
    bands are a fraction of a file's length, so they differ per file. Each is
    sampled on its own and the samples joined, so the totals below sum across
    every file.
    """

    interval_frames: int
    region_frames: int
    sources: tuple[SampledSource, ...]

    @property
    def num_samples(self) -> int:
        """Number of samples that will be generated across every file."""
        return sum(
            (s.usable_range.frame_count + self.interval_frames - self.region_frames)
            // self.interval_frames
            for s in self.sources
        )

    @property
    def total_sample_frames(self) -> int:
        """Total number of frames across all samples."""
        return self.num_samples * self.region_frames


def generate_metric_reference(
    metric_type: Literal["vmaf", "ssim2", "shared"],
    output_dir: Path,
    sampling_params: MetricSamplingParams,
    fps: float,
    lossless_profile: Profile,
    video_info: VideoInfo,
    mkvmerge_bin: str,
    repo_root: Path,
    temp_dir: Path,
    display: PipelineDisplay,
    log: logging.Logger,
    crop_detect: bool = False,
    crop_values: CropValues | None = None,
) -> Path | None:
    """Generate a concatenated reference file for quality metric assessment.

    This function handles both VMAF and SSIM2 reference generation using the same
    logic, reducing code duplication.

    Args:
        metric_type: Type of metric ("vmaf" or "ssim2")
        output_dir: Directory for reference output
        sampling_params: Parameters controlling periodic sampling
        fps: Video frame rate
        lossless_profile: Profile for lossless encoding
        video_info: Video metadata
        mkvmerge_bin: Path to mkvmerge binary
        repo_root: Repository root directory
        temp_dir: Temporary directory for intermediate files
        display: Pipeline display for progress UI
        log: Logger for status messages
        crop_detect: Whether to apply cropdetect
        crop_values: Crop values for cropping

    Returns:
        Path to the generated reference MKV, or None if generation failed
    """
    from .create_encodes import encode_concatenated_reference, mux_to_mkv

    metric_label = (
        metric_type.title() if metric_type == "shared" else metric_type.upper()
    )
    ref_path = output_dir / f"{metric_type}_reference_concatenated.mkv"
    total_frames = sampling_params.total_sample_frames

    bitstream_path: Path | None = None

    # Encode reference bitstream
    with display.stage(
        f"Encoding {metric_label} reference",
        total=total_frames,
        unit="frames",
        transient=True,
        show_done=True,
    ) as enc_stage:
        enc_handler = enc_stage.make_encoder_handler(
            total_frames=total_frames,
            encoder_type=lossless_profile.encoder,
        )

        try:
            bitstream_path = encode_concatenated_reference(
                sources=sampling_params.sources,
                output_path=ref_path,
                interval_frames=sampling_params.interval_frames,
                region_frames=sampling_params.region_frames,
                fps=fps,
                profile=lossless_profile,
                video_info=video_info,
                mkvmerge_bin=mkvmerge_bin,
                cwd=repo_root,
                temp_dir=temp_dir,
                line_handler=enc_handler,
                mux_handler=None,
                perform_mux=False,
                enable_cropdetect=crop_detect,
                crop_values=crop_values,
                metric_label=metric_label,
            )
            log.info(
                "%s concatenated reference bitstream created: %s",
                metric_label,
                bitstream_path.name,
            )
        except Exception as e:
            log.error("Failed to create %s reference: %s", metric_label, e)
            _cleanup_bitstream(bitstream_path)
            return None

    # bitstream_path is guaranteed to be Path here (exception would have returned None)
    assert bitstream_path is not None

    # Mux bitstream to MKV
    with display.stage(
        f"Muxing {metric_label} reference",
        total=100,
        unit="%",
        transient=True,
        show_done=True,
    ) as mux_stage:
        mux_handler = mux_stage.make_percent_handler()

        try:
            mux_to_mkv(
                bitstream_path=bitstream_path,
                output_path=ref_path,
                mkvmerge_bin=mkvmerge_bin,
                cwd=repo_root,
                line_handler=mux_handler,
            )
            log.info(
                "%s concatenated reference created: %s",
                metric_label,
                ref_path.name,
            )
        except Exception as e:
            log.error("Failed to mux %s reference: %s", metric_label, e)
            return None
        finally:
            _cleanup_bitstream(bitstream_path)

    return ref_path


def _cleanup_bitstream(bitstream_path: Path | None) -> None:
    """Safely remove bitstream intermediate file."""
    if bitstream_path is not None and bitstream_path.exists():
        try:
            bitstream_path.unlink()
        except OSError as e:
            logging.getLogger(__name__).debug(
                "Failed to delete temporary bitstream file %s: %s", bitstream_path, e
            )
