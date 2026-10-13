"""How a job decides whether its two metrics can share one encode.

Both metrics score against frames selected from the source by the sampling
script, so nothing is generated here. What is left is the question of whether
VMAF and SSIMULACRA2 select the same frames, which decides whether one
distorted encode serves both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline_cli import PipelineArgs


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
