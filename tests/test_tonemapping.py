"""Tests for tonemapping module."""

from __future__ import annotations

import subprocess
from typing import cast
from unittest.mock import MagicMock, patch

from videotuner.tonemapping import (
    build_tonemap_chain,
    format_tonemap_backend,
    has_vulkan_support,
    vulkan_device_is_hardware,
)

# Real output from `ffmpeg -v verbose -init_hw_device vulkan`. FFmpeg formats the
# selected device as "Device %d selected: %s (%s) (0x%x)", where the second %s is
# one of integrated/discrete/virtual/software/unknown (hwcontext_vulkan.c).
DISCRETE_GPU = (
    "[Vulkan @ 000001] Device 0 selected: Example Discrete GPU (discrete) (0x7550)\n"
    "[Vulkan @ 000001] Using device: Example Discrete GPU\n"
)
SOFTWARE_RASTERISER = (
    "[Vulkan @ 000001]     0: llvmpipe (LLVM 17.0.6, 256 bits) (software) (0x0)\n"
    "[Vulkan @ 000001] Device 0 selected: llvmpipe (LLVM 17.0.6, 256 bits)"
    " (software) (0x0)\n"
)


class TestVulkanDeviceIsHardware:
    """A software Vulkan device is slower than the CPU path it would displace."""

    def test_software_rasteriser_is_not_hardware(self) -> None:
        assert vulkan_device_is_hardware(SOFTWARE_RASTERISER) is False

    def test_software_listed_but_real_gpu_selected(self) -> None:
        """Mesa installs lavapipe alongside real drivers, so it is often
        enumerated on a machine that then selects a real GPU."""
        probe = (
            "[Vulkan @ 000001]     0: llvmpipe (LLVM 17.0.6, 256 bits)"
            " (software) (0x0)\n"
            "[Vulkan @ 000001]     1: Example Discrete GPU (discrete) (0x7550)\n"
            "[Vulkan @ 000001] Device 1 selected: Example Discrete GPU"
            " (discrete) (0x7550)\n"
        )
        assert vulkan_device_is_hardware(probe) is True

    def test_discrete_gpu_is_hardware(self) -> None:
        assert vulkan_device_is_hardware(DISCRETE_GPU) is True

    def test_integrated_gpu_is_hardware(self) -> None:
        probe = (
            "[Vulkan @ 000001] Device 0 selected: Example Integrated GPU"
            " (integrated) (0x9a60)\n"
        )
        assert vulkan_device_is_hardware(probe) is True

    def test_unrecognised_device_type_is_treated_as_hardware(self) -> None:
        """Failing open: the probe's exit status already proved Vulkan works, so an
        unfamiliar type should not cost a working GPU."""
        probe = (
            "[Vulkan @ 0000026d994ff280] Device 0 selected: Some Future GPU"
            " (unknown) (0x1234)\n"
        )
        assert vulkan_device_is_hardware(probe) is True

    def test_missing_device_line_is_treated_as_hardware(self) -> None:
        assert vulkan_device_is_hardware("") is True


class TestHasVulkanSupport:
    """Tests for Vulkan GPU detection."""

    def setup_method(self) -> None:
        # Clear the lru_cache between tests
        has_vulkan_support.cache_clear()

    def test_vulkan_available(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = DISCRETE_GPU
        with patch("videotuner.tonemapping.subprocess.run", return_value=mock_result):
            assert has_vulkan_support("ffmpeg") is True

    def test_vulkan_not_available(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("videotuner.tonemapping.subprocess.run", return_value=mock_result):
            assert has_vulkan_support("ffmpeg") is False

    def test_ffmpeg_not_found(self) -> None:
        with patch(
            "videotuner.tonemapping.subprocess.run", side_effect=FileNotFoundError
        ):
            assert has_vulkan_support("nonexistent") is False

    def test_timeout(self) -> None:
        with patch(
            "videotuner.tonemapping.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10),
        ):
            assert has_vulkan_support("ffmpeg") is False

    def test_software_device_reports_no_vulkan_support(self) -> None:
        """The probe succeeds on a software rasteriser, so exit status alone is not
        enough to decide the GPU path is worth taking."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = SOFTWARE_RASTERISER
        with patch("videotuner.tonemapping.subprocess.run", return_value=mock_result):
            assert has_vulkan_support("ffmpeg") is False

    def test_caching(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = DISCRETE_GPU
        with patch(
            "videotuner.tonemapping.subprocess.run", return_value=mock_result
        ) as mock_run:
            assert has_vulkan_support("ffmpeg") is True
            assert has_vulkan_support("ffmpeg") is True
            # Should only be called once due to lru_cache
            mock_run.assert_called_once()

    def test_probe_command_structure(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = DISCRETE_GPU
        with patch(
            "videotuner.tonemapping.subprocess.run", return_value=mock_result
        ) as mock_run:
            _ = has_vulkan_support("ffmpeg")
            assert mock_run.call_args is not None
            cmd = cast(list[str], mock_run.call_args[0][0])
            kwargs = cast(dict[str, object], mock_run.call_args[1])
            assert cmd[0] == "ffmpeg"
            assert "-init_hw_device" in cmd
            assert "vulkan" in cmd
            assert kwargs["timeout"] == 10

    def test_probe_requests_verbose_output(self) -> None:
        """The device type only appears at verbose level, so a quiet probe cannot
        tell a GPU from a software rasteriser."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = DISCRETE_GPU
        with patch(
            "videotuner.tonemapping.subprocess.run", return_value=mock_result
        ) as mock_run:
            _ = has_vulkan_support("ffmpeg")
            assert mock_run.call_args is not None
            cmd = cast(list[str], mock_run.call_args[0][0])
            assert "verbose" in cmd
            assert "quiet" not in cmd


class TestFormatTonemapBackend:
    """The backend moves HDR scores, so a result is not interpretable without it."""

    def test_gpu_names_libplacebo(self) -> None:
        assert format_tonemap_backend(use_gpu=True) == "GPU (libplacebo)"

    def test_cpu_names_its_operator(self) -> None:
        assert format_tonemap_backend(use_gpu=False) == "CPU (zscale + hable)"


class TestBuildTonemapChain:
    """Tests for tonemapping filter chain building."""

    def test_gpu_path_contains_libplacebo(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=True)
        assert "libplacebo=" in chain

    def test_gpu_path_contains_bt2390(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=True)
        assert "tonemapping=bt.2390" in chain

    def test_gpu_path_peak_detect_off(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=True)
        assert "peak_detect=0" in chain

    def test_gpu_path_bt709_output(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=True)
        assert "colorspace=bt709" in chain
        assert "color_primaries=bt709" in chain
        assert "color_trc=bt709" in chain

    def test_gpu_path_dimensions(self) -> None:
        chain = build_tonemap_chain(1920, 1080, use_gpu=True)
        assert "w=1920" in chain
        assert "h=1080" in chain

    def test_gpu_path_limited_range(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=True)
        assert "range=limited" in chain

    def test_cpu_path_contains_zscale(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=False)
        assert "zscale=" in chain

    def test_cpu_path_contains_hable(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=False)
        assert "tonemap=hable" in chain

    def test_cpu_path_dimensions(self) -> None:
        chain = build_tonemap_chain(1920, 1080, use_gpu=False)
        assert "scale=1920:1080" in chain

    def test_cpu_path_npl(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=False)
        assert "npl=100" in chain

    def test_cpu_path_desat(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=False)
        assert "desat=2" in chain

    def test_cpu_path_no_libplacebo(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=False)
        assert "libplacebo" not in chain

    def test_gpu_path_no_zscale(self) -> None:
        chain = build_tonemap_chain(3840, 2160, use_gpu=True)
        assert "zscale" not in chain
