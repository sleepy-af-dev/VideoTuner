"""VMAF model generation types.

Separate from ``vmaf_assessment`` so that modules describing a run can name the
generation without importing the assessment code, which reaches back into
``pipeline_types`` for its output folders. Same reason ``encoder_type`` stands
on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VmafFamily(StrEnum):
    """Which generation of VMAF models a run is using."""

    V0 = "v0"
    V1 = "v1"


@dataclass(frozen=True)
class VmafModelChoice:
    """The model generation for a run, settled once before any comparison.

    Attributes:
        family: Generation in use, which is what the settings header reports.
        explicit_model: Model the user named, which bypasses per-comparison
            selection entirely.
        fell_back: True when v1 was wanted and the probe did not find it.
    """

    family: VmafFamily
    explicit_model: str | None = None
    fell_back: bool = False
