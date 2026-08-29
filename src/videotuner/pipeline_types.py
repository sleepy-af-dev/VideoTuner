"""Shared types and path utilities for the VideoTuner pipeline.

This module contains dataclasses and path management functions shared across
pipeline modules to avoid circular imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .encoding_utils import CropValues, SampledSource
from .media import VideoInfo
from .pipeline_cli import PipelineArgs
from .profiles import Profile
from .progress import PipelineDisplay
from .utils import ensure_dir


@dataclass
class IterationContext:
    """Shared context for pipeline iterations."""

    # Paths. ``input_path`` names the job: the file it reads, or the folder
    # when several files are read as one source. ``sources`` is what is
    # actually decoded, one entry per file with its own usable range.
    input_path: Path
    sources: list[SampledSource]
    workdir: Path
    temp_dir: Path
    repo_root: Path

    # Video info
    info: VideoInfo

    # Profile and settings
    selected_profile: Profile

    # Periodic sampling parameters
    total_frames: int
    guard_start_frames: int
    guard_end_frames: int

    # Reference files (single concatenated file per metric)
    vmaf_ref_path: Path | None
    ssim2_ref_path: Path | None

    # Command-line args
    args: PipelineArgs

    # Display
    display: PipelineDisplay
    log: logging.Logger

    # CropDetect values (calculated once, shared across all encodes)
    crop_values: CropValues | None = None

    # Sample sharing: True when VMAF and SSIM2 use identical sampling parameters
    sharing_samples: bool = False

    @property
    def usable_frames(self) -> int:
        """Number of frames available for sampling (excluding guard bands)."""
        return self.total_frames - self.guard_start_frames - self.guard_end_frames


@dataclass(frozen=True)
class JobResult:
    """Outcome of one job, for the caller's exit code and the batch summary.

    A job is one source video processed end to end. ``ok`` drives the exit code;
    ``status`` is the human-readable cell shown in the batch summary table.
    """

    input_path: Path
    ok: bool
    status: str = "ok"
    profile_name: str | None = None
    optimal_crf: float | None = None
    predicted_bitrate_kbps: float = 0.0
    source_bitrate_kbps: float | None = None
    scores: dict[str, float | None] = field(default_factory=dict)

    @classmethod
    def failure(cls, input_path: Path, status: str) -> JobResult:
        """Build a failed result. ``status`` is shown verbatim in the summary."""
        return cls(input_path=input_path, ok=False, status=status)


@dataclass(frozen=True)
class MultiProfileResult:
    """Results from a single profile in multi-profile mode (CRF search or bitrate).

    For CRF profiles: optimal_crf contains the searched value, meets_all_targets is bool
    For bitrate profiles: optimal_crf is None, meets_all_targets is True/False when
        targets are specified, or None when no targets exist
    """

    profile_name: str
    optimal_crf: float | None  # None if failed to converge or bitrate mode
    scores: dict[
        str, float | None
    ]  # Final metric scores (None for unavailable metrics)
    predicted_bitrate_kbps: float  # Predicted bitrate across all samples
    converged: bool  # True if search converged successfully
    meets_all_targets: bool | None = None  # True/False for CRF, None for bitrate (N/A)

    @property
    def is_bitrate_mode(self) -> bool:
        """Check if this result is from a bitrate profile (not CRF)."""
        return self.optimal_crf is None

    def is_valid(self) -> bool:
        """Check if this profile produced valid results.

        For CRF profiles: converged and has optimal_crf
        For bitrate profiles: converged (optimal_crf will be None)
        """
        return self.converged


# Path management utilities


#: Directories a job creates for its own use. A profile directory sits beside
#: them, so a profile named after one of these is renamed to keep them apart.
RESERVED_JOB_SUBDIRS: frozenset[str] = frozenset({"reference", "temp"})


def _profile_slug(profile: Profile) -> str:
    """Convert profile name to filesystem-safe slug."""
    slug = profile.name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    if slug.casefold() in RESERVED_JOB_SUBDIRS:
        # Case-insensitive: Windows would treat "Temp" and "temp" as one folder.
        return f"{slug}_profile"
    return slug


def get_reference_dir(workdir: Path) -> Path:
    """Get path to reference files directory, creating if needed.

    Args:
        workdir: Working directory for the job

    Returns:
        Path to reference directory
    """
    ref_dir = workdir / "reference"
    return ensure_dir(ref_dir)


def get_profile_dir(workdir: Path, profile: Profile) -> Path:
    """Get the directory holding everything produced for one profile.

    Encodes and both metrics' results share a directory because their filenames
    already say which is which. Splitting them into distorted/, vmaf/ and
    ssimulacra2/ trees scattered one profile's output across three places and
    cost a level of nesting that the path budget could not spare.

    Args:
        workdir: Working directory for the job
        profile: Encoding profile (used for directory naming)

    Returns:
        Path to this profile's directory
    """
    return ensure_dir(workdir / _profile_slug(profile))


def get_distorted_dir(workdir: Path, profile: Profile) -> Path:
    """Get path to distorted files for a profile, creating the directory."""
    return get_profile_dir(workdir, profile)


def get_vmaf_dir(workdir: Path, profile: Profile) -> Path:
    """Get path to VMAF results for a profile, creating the directory."""
    return get_profile_dir(workdir, profile)


def get_ssim2_dir(workdir: Path, profile: Profile) -> Path:
    """Get path to SSIMULACRA2 results for a profile, creating the directory."""
    return get_profile_dir(workdir, profile)
