"""Tests for media module."""

from __future__ import annotations

from pathlib import Path

from videotuner.media import (
    VideoFormat,
    VideoInfo,
    combine_video_info,
    estimate_frame_count,
    get_bit_depth_from_pix_fmt,
    get_frame_count,
    get_video_format,
)
from videotuner.tool_parsers import parse_fraction


class TestParseFfprobeRate:
    """Tests for FFprobe frame rate parsing."""

    def test_parses_fraction_rate(self):
        """Test parsing fractional frame rate like 24000/1001."""
        result = parse_fraction("24000/1001")
        assert abs(result - 23.976) < 0.001

    def test_parses_simple_fraction(self):
        """Test parsing simple fraction like 30/1."""
        result = parse_fraction("30/1")
        assert result == 30.0

    def test_parses_decimal_rate(self):
        """Test parsing decimal rate like 29.97."""
        result = parse_fraction("29.97")
        assert result == 29.97

    def test_handles_division_by_zero(self):
        """Test that division by zero returns 0."""
        result = parse_fraction("30/0")
        assert result == 0.0

    def test_handles_invalid_format(self):
        """Test that invalid format returns 0."""
        result = parse_fraction("invalid")
        assert result == 0.0

    def test_handles_malformed_fraction(self):
        """Test that malformed fraction returns 0."""
        result = parse_fraction("abc/def")
        assert result == 0.0


class TestGetBitDepthFromPixFmt:
    """Tests for bit depth extraction from pixel format."""

    def test_extracts_10bit_from_pix_fmt(self):
        """Test extracting 10-bit depth."""
        assert get_bit_depth_from_pix_fmt("yuv420p10le") == 10
        assert get_bit_depth_from_pix_fmt("yuv444p10le") == 10

    def test_extracts_12bit_from_pix_fmt(self):
        """Test extracting 12-bit depth."""
        assert get_bit_depth_from_pix_fmt("yuv420p12le") == 12
        assert get_bit_depth_from_pix_fmt("yuv444p12le") == 12

    def test_defaults_to_8bit_for_no_suffix(self):
        """Test that formats without bit depth suffix default to 8-bit."""
        assert get_bit_depth_from_pix_fmt("yuv420p") == 8
        assert get_bit_depth_from_pix_fmt("yuv444p") == 8

    def test_handles_none_pix_fmt(self):
        """Test that None pix_fmt defaults to 8-bit."""
        assert get_bit_depth_from_pix_fmt(None) == 8

    def test_handles_empty_string(self):
        """Test that empty string defaults to 8-bit."""
        assert get_bit_depth_from_pix_fmt("") == 8

    def test_ignores_invalid_bit_depths(self):
        """Test that unsupported bit depths (not 8/10/12/14/16) default to 8."""
        assert get_bit_depth_from_pix_fmt("yuv420p6le") == 8
        assert get_bit_depth_from_pix_fmt("yuv420p9le") == 8

    def test_supports_14bit_and_16bit(self):
        """Test that 14-bit and 16-bit formats are properly detected."""
        assert get_bit_depth_from_pix_fmt("yuv420p14le") == 14
        assert get_bit_depth_from_pix_fmt("yuv420p16le") == 16

    def test_semi_planar_names_subsampling_before_depth(self):
        """p010le is 4:2:0 10-bit, p210le is 4:2:2 10-bit, p410le is 4:4:4
        10-bit. The digit after the p is the chroma layout, not part of the
        depth, so reading all three digits as a number gives 210 and 410."""
        assert get_bit_depth_from_pix_fmt("p010le") == 10
        assert get_bit_depth_from_pix_fmt("p210le") == 10
        assert get_bit_depth_from_pix_fmt("p410le") == 10

    def test_semi_planar_higher_depths(self):
        assert get_bit_depth_from_pix_fmt("p012le") == 12
        assert get_bit_depth_from_pix_fmt("p016le") == 16
        assert get_bit_depth_from_pix_fmt("p216le") == 16

    def test_packed_rgb_counts_bits_per_pixel(self):
        """Packed RGB names the total across components, so the depth is that
        divided by the component count."""
        assert get_bit_depth_from_pix_fmt("rgb24") == 8
        assert get_bit_depth_from_pix_fmt("bgr24") == 8
        assert get_bit_depth_from_pix_fmt("rgb48le") == 16
        assert get_bit_depth_from_pix_fmt("bgra64le") == 16

    def test_planar_rgb_still_reads_the_plane_marker(self):
        """gbrp10le is planar and keeps the depth after the p, so the packed
        rule must not claim it."""
        assert get_bit_depth_from_pix_fmt("gbrp10le") == 10
        assert get_bit_depth_from_pix_fmt("gbrp") == 8


class TestEstimateFrameCount:
    """Only reached when the container metadata carries no frame count."""

    def test_prefers_the_track_duration(self):
        """Figures from an input whose container metadata reports 2881 frames:
        the video track runs 120.162s and the container 121.955s. The track
        duration reproduces 2881 exactly; the container's gives 2923, which is
        42 frames past the end of the video."""
        assert (
            estimate_frame_count(
                23.976023976023978,
                track_duration=120.162,
                container_duration=121.955,
            )
            == 2881
        )

    def test_falls_back_to_the_container_duration(self):
        assert (
            estimate_frame_count(25.0, track_duration=None, container_duration=4.0)
            == 100
        )

    def test_no_usable_duration_gives_nothing(self):
        assert (
            estimate_frame_count(25.0, track_duration=None, container_duration=0.0)
            is None
        )

    def test_no_usable_fps_gives_nothing(self):
        assert (
            estimate_frame_count(0.0, track_duration=10.0, container_duration=10.0)
            is None
        )

    def test_rounds_down(self):
        """Overshooting asks the sampler for frames past the end of the video,
        so a fractional result truncates rather than rounding."""
        assert (
            estimate_frame_count(25.0, track_duration=4.999, container_duration=4.999)
            == 124
        )


class TestGetVideoFormat:
    """Tests for video format detection (HDR vs SDR)."""

    def test_ffprobe_smpte2084_is_hdr(self):
        """ffprobe spells PQ 'smpte2084'. parse_video_info fills color_trc
        straight from ffprobe, so this is the only spelling a real run sees."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="smpte2084")
        assert get_video_format(video_info) == VideoFormat.HDR

    def test_ffprobe_arib_std_b67_is_hdr(self):
        """ffprobe spells HLG 'arib-std-b67', hyphenated."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="arib-std-b67")
        assert get_video_format(video_info) == VideoFormat.HDR

    def test_ffprobe_bt709_is_sdr(self):
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="bt709")
        assert get_video_format(video_info) == VideoFormat.SDR

    def test_ffprobe_wide_gamut_sdr_is_sdr(self):
        """A BT.2020 SDR transfer is wide gamut, not high dynamic range."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="bt2020-10")
        assert get_video_format(video_info) == VideoFormat.SDR

    # The cases below use pymediainfo's spellings, which parse_video_info does
    # not produce; HDR_TRANSFER_CHARACTERISTICS accepts them deliberately so a
    # VideoInfo assembled from MediaInfo still classifies. The ffprobe cases
    # above are the ones that cover a real run.

    def test_pq_transfer_is_hdr(self):
        """Test that PQ transfer characteristic is detected as HDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="PQ")
        assert get_video_format(video_info) == VideoFormat.HDR

    def test_smpte2084_transfer_is_hdr(self):
        """Test that SMPTE 2084 transfer is detected as HDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="SMPTE 2084")
        assert get_video_format(video_info) == VideoFormat.HDR

    def test_hlg_transfer_is_hdr(self):
        """Test that HLG transfer characteristic is detected as HDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="HLG")
        assert get_video_format(video_info) == VideoFormat.HDR

    def test_arib_std_b67_transfer_is_hdr(self):
        """Test that ARIB STD-B67 transfer is detected as HDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="ARIB STD-B67")
        assert get_video_format(video_info) == VideoFormat.HDR

    def test_bt709_transfer_is_sdr(self):
        """Test that BT.709 transfer is detected as SDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="BT.709")
        assert get_video_format(video_info) == VideoFormat.SDR

    def test_none_transfer_is_sdr(self):
        """Test that None transfer defaults to SDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc=None)
        assert get_video_format(video_info) == VideoFormat.SDR

    def test_unknown_transfer_is_sdr(self):
        """Test that unknown transfer defaults to SDR."""
        video_info = VideoInfo(fps=24.0, duration=100.0, color_trc="unknown")
        assert get_video_format(video_info) == VideoFormat.SDR


class TestVideoInfo:
    """Tests for VideoInfo dataclass."""

    def test_video_info_basic_creation(self):
        """Test creating VideoInfo with basic fields."""
        info = VideoInfo(
            fps=29.97,
            duration=3600.0,
            pix_fmt="yuv420p10le",
            width=1920,
            height=1080,
        )

        assert info.fps == 29.97
        assert info.duration == 3600.0
        assert info.pix_fmt == "yuv420p10le"
        assert info.width == 1920
        assert info.height == 1080

    def test_video_info_with_hdr_metadata(self):
        """Test VideoInfo with HDR metadata fields."""
        info = VideoInfo(
            fps=24.0,
            duration=7200.0,
            color_trc="smpte2084",
            color_primaries="bt2020",
            mastering_display_color_primaries="Display P3",
            mastering_display_luminance="min: 0.0050 cd/m2, max: 1000 cd/m2",
            maximum_content_light_level="1000 cd/m2",
            maximum_frameaverage_light_level="400 cd/m2",
        )

        assert info.color_trc == "smpte2084"
        assert info.color_primaries == "bt2020"
        assert info.mastering_display_color_primaries == "Display P3"
        assert info.mastering_display_luminance == "min: 0.0050 cd/m2, max: 1000 cd/m2"

    def test_video_info_defaults_to_none(self):
        """Test that optional VideoInfo fields default to None."""
        info = VideoInfo(fps=24.0, duration=100.0)

        assert info.pix_fmt is None
        assert info.width is None
        assert info.height is None
        assert info.color_primaries is None
        assert info.color_trc is None
        assert info.color_space is None
        assert info.color_range is None
        assert info.chroma_location is None
        assert info.video_bitrate_kbps is None


class TestGetFrameCount:
    """Tests for get_frame_count function."""

    def test_returns_frame_count_from_video_info(self):
        """Test returns frame_count when available in VideoInfo."""
        info = VideoInfo(fps=24.0, duration=100.0, frame_count=2400)
        result = get_frame_count(Path("/nonexistent/video.mkv"), info)
        assert result == 2400

    def test_returns_frame_count_with_fractional_fps(self):
        """Test returns correct frame_count with fractional frame rates."""
        info = VideoInfo(fps=23.976, duration=100.0, frame_count=2397)
        result = get_frame_count(Path("/nonexistent/video.mkv"), info)
        assert result == 2397

    def test_returns_1_when_no_info_provided(self):
        """Test returns minimum of 1 when no VideoInfo provided."""
        result = get_frame_count(Path("/nonexistent/video.mkv"))
        assert result == 1

    def test_returns_1_when_info_has_no_fps(self):
        """Test returns 1 when VideoInfo lacks fps."""
        info = VideoInfo(fps=0.0, duration=100.0)
        result = get_frame_count(Path("/nonexistent/video.mkv"), info)
        assert result == 1

    def test_returns_1_when_info_has_no_duration(self):
        """Test returns 1 when VideoInfo lacks duration."""
        info = VideoInfo(fps=24.0, duration=0.0)
        result = get_frame_count(Path("/nonexistent/video.mkv"), info)
        assert result == 1

    def test_minimum_frame_count_is_1(self):
        """Test that frame count is never less than 1."""
        info = VideoInfo(fps=0.001, duration=0.001)
        result = get_frame_count(Path("/nonexistent/video.mkv"), info)
        assert result >= 1


def _info(
    duration: float,
    *,
    frame_count: int | None = None,
    bitrate: float | None = None,
    width: int = 3840,
    height: int = 2160,
    fps: float = 24.0,
) -> VideoInfo:
    return VideoInfo(
        fps=fps,
        duration=duration,
        width=width,
        height=height,
        pix_fmt="yuv420p10le",
        color_trc="smpte2084",
        frame_count=frame_count,
        video_bitrate_kbps=bitrate,
    )


class TestCombineVideoInfo:
    """Describing several files as the one source a job reads."""

    def test_a_single_info_is_returned_unchanged(self) -> None:
        info = _info(100.0, frame_count=2400, bitrate=50_000)
        assert combine_video_info([info]) == info

    def test_duration_and_frame_count_are_summed(self) -> None:
        combined = combine_video_info(
            [_info(100.0, frame_count=2400), _info(50.0, frame_count=1200)]
        )

        assert combined.duration == 150.0
        assert combined.frame_count == 3600

    def test_format_facts_come_from_the_first_file(self) -> None:
        combined = combine_video_info([_info(100.0), _info(50.0)])

        assert combined.fps == 24.0
        assert combined.width == 3840
        assert combined.height == 2160
        assert combined.pix_fmt == "yuv420p10le"
        assert combined.color_trc == "smpte2084"

    def test_bitrate_is_weighted_by_duration(self) -> None:
        """A short high-bitrate file must not outweigh a long one."""
        combined = combine_video_info(
            [_info(100.0, bitrate=10_000), _info(300.0, bitrate=50_000)]
        )

        # (10000*100 + 50000*300) / 400 = 40000
        assert combined.video_bitrate_kbps == 40_000

    def test_files_without_a_bitrate_are_left_out_of_the_weighting(self) -> None:
        combined = combine_video_info(
            [_info(100.0, bitrate=10_000), _info(100.0, bitrate=None)]
        )

        assert combined.video_bitrate_kbps == 10_000

    def test_no_bitrate_anywhere_stays_unknown(self) -> None:
        combined = combine_video_info([_info(100.0), _info(50.0)])
        assert combined.video_bitrate_kbps is None

    def test_an_unknown_frame_count_makes_the_total_unknown(self) -> None:
        combined = combine_video_info(
            [_info(100.0, frame_count=2400), _info(50.0, frame_count=None)]
        )
        assert combined.frame_count is None
