"""Tests for EncoderType enum."""

from videotuner.encoder_type import EncoderType


class TestEncoderType:
    """Tests for EncoderType enum properties."""

    def test_x265_value(self):
        """Test x265 enum value."""
        assert EncoderType.X265.value == "x265"

    def test_x264_value(self):
        """Test x264 enum value."""
        assert EncoderType.X264.value == "x264"

    def test_x265_bitstream_extension(self):
        """Test x265 bitstream extension is .hevc."""
        assert EncoderType.X265.bitstream_extension == ".hevc"

    def test_x264_bitstream_extension(self):
        """Test x264 bitstream extension is .264."""
        assert EncoderType.X264.bitstream_extension == ".264"

    def test_string_construction(self):
        """Test EncoderType can be constructed from string."""
        assert EncoderType("x265") == EncoderType.X265
        assert EncoderType("x264") == EncoderType.X264

    def test_is_string_subclass(self):
        """Test EncoderType is a string (str, Enum)."""
        assert isinstance(EncoderType.X265, str)
        assert isinstance(EncoderType.X264, str)
