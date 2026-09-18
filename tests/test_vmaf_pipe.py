from __future__ import annotations

from videotuner.vmaf_assessment import build_vmaf_filter


def test_the_filter_reads_two_tracks_from_one_input() -> None:
    # One pipe carries both, so they arrive as [0:v:0] and [0:v:1] rather
    # than [0:v] and [1:v].
    graph = build_vmaf_filter(width=1920, height=1080)
    assert "[0:v:0]" in graph
    assert "[0:v:1]" in graph
    assert "[1:v]" not in graph


def test_the_filter_no_longer_tonemaps() -> None:
    # VapourSynth tonemaps before ffmpeg sees a frame.
    graph = build_vmaf_filter(width=1920, height=1080)
    assert "libplacebo" not in graph
    assert "zscale" not in graph
    assert "hable" not in graph


def test_the_filter_still_scales_the_distorted_track() -> None:
    # A downscaled encode is scaled back to reference resolution, and that
    # stays in ffmpeg: changing the resampler alone moved VMAF 1.78 points.
    graph = build_vmaf_filter(width=1920, height=1080)
    assert "scale=1920:1080:flags=bicubic" in graph


def test_v1_measures_at_ten_bit_whatever_the_encode_depth() -> None:
    assert "yuv420p10le" in build_vmaf_filter(width=1920, height=1080, dis_bit_depth=8)


def test_v0_matches_the_encode_depth() -> None:
    graph = build_vmaf_filter(width=1920, height=1080, dis_bit_depth=8, use_v0=True)
    assert "format=yuv420p," in graph
    assert "yuv420p10le" not in graph
