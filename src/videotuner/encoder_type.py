"""Encoder type enumeration for VideoTuner."""

from __future__ import annotations

from enum import StrEnum


class EncoderType(StrEnum):
    """Supported video encoder types."""

    X265 = "x265"
    X264 = "x264"

    @property
    def bitstream_extension(self) -> str:
        """File extension for raw bitstream output."""
        return {"x265": ".hevc", "x264": ".264"}[self.value]
