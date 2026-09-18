"""Unified tonemapping helpers for HDR→SDR conversion.

Provides GPU detection and FFmpeg filter chain construction for tonemapping.
Used by both VMAF assessment and cropdetect to ensure consistent HDR handling.

GPU path uses libplacebo with bt.2390 (Vulkan required).
CPU path uses zscale + tonemap with hable (no GPU required).
"""

from __future__ import annotations

import logging
import re
import subprocess
from functools import lru_cache

from .utils import warn_user

logger = logging.getLogger(__name__)


# FFmpeg logs the chosen device as "Device %d selected: %s (%s) (0x%x)", where the
# bracketed word is integrated/discrete/virtual/software/unknown. Only the selected
# device matters: Mesa enumerates lavapipe alongside real drivers, so a machine with
# a working GPU routinely lists a software device too.
_VULKAN_SELECTED_DEVICE = re.compile(
    r"Device \d+ selected:.*\((\w+)\) \(0x[0-9a-fA-F]+\)"
)


def vulkan_device_is_hardware(probe_output: str) -> bool:
    """Decide whether FFmpeg's Vulkan probe selected a real GPU.

    A software rasteriser such as lavapipe presents a conformant Vulkan device
    implemented entirely on the CPU. Running libplacebo on one is slower than the
    zscale path it would displace, so it is worse than having no GPU at all.

    Args:
        probe_output: stderr from ``ffmpeg -v verbose -init_hw_device vulkan``.

    Returns:
        False only on positive evidence that the selected device is software.
        Anything unrecognised is treated as hardware, since the probe's exit
        status has already shown that Vulkan works.
    """
    match = _VULKAN_SELECTED_DEVICE.search(probe_output)
    if match is None:
        return True
    return match.group(1) != "software"


@lru_cache(maxsize=1)
def has_vulkan_support(ffmpeg_bin: str = "ffmpeg") -> bool:
    """Probe whether FFmpeg can initialise a Vulkan device.

    The result is cached for the lifetime of the process so the probe
    runs at most once.

    Args:
        ffmpeg_bin: Path to the ffmpeg binary.

    Returns:
        True only if Vulkan initialises *and* selects a hardware device. A
        software rasteriser is reported as unsupported, since libplacebo on one
        is slower than the CPU path it would displace.
    """
    # -v verbose so the probe reports which device it chose; exit status alone
    # cannot tell a real GPU from a CPU implementation of Vulkan.
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-v",
        "verbose",
        "-init_hw_device",
        "vulkan",
        "-f",
        "lavfi",
        "-i",
        "nullsrc=s=16x16:d=0.04",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    software_device = False
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=10,
        )
        supported = result.returncode == 0
        if supported and not vulkan_device_is_hardware(result.stderr or ""):
            supported = False
            software_device = True
    except FileNotFoundError:
        warn_user(
            logger, "ffmpeg not found at '%s'; assuming no Vulkan support", ffmpeg_bin
        )
        supported = False
    except subprocess.TimeoutExpired:
        warn_user(logger, "Vulkan probe timed out; assuming no Vulkan support")
        supported = False

    if supported:
        logger.info("Vulkan GPU detected — using libplacebo (bt.2390) for tonemapping")
    elif software_device:
        logger.info(
            "Vulkan reported a software device, not a GPU — "
            + "using CPU tonemapping (zscale + hable), which is faster there"
        )
    else:
        logger.info("No Vulkan GPU — using CPU tonemapping (zscale + hable)")

    return supported


def build_tonemap_chain(width: int, height: int, *, use_gpu: bool) -> str:
    """Build an FFmpeg filter chain string for HDR→SDR tonemapping.

    Peak detection is always disabled for reproducible, frame-perfect results.

    Args:
        width: Target output width in pixels.
        height: Target output height in pixels.
        use_gpu: If True, use libplacebo via Vulkan; otherwise use zscale + hable.

    Returns:
        FFmpeg filter chain string (comma-separated filters).
    """
    if use_gpu:
        # libplacebo via Vulkan — bt.2390, peak detection off
        # gamut_mapping defaults to 'perceptual'
        return (
            f"libplacebo=w={width}:h={height}"
            ":colorspace=bt709:color_primaries=bt709:color_trc=bt709"
            ":range=limited:tonemapping=bt.2390:peak_detect=0"
        )

    # CPU fallback — zscale + tonemap (hable)
    # npl=100: SDR reference level (100 nits) for PQ linearisation
    # desat=2: FFmpeg default, desaturates very bright colours naturally
    # peak=0: auto-detect signal peak from HDR metadata
    return (
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        f"tonemap=hable:desat=2:peak=0,zscale=t=bt709:m=bt709:r=tv,"
        f"scale={width}:{height}:flags=bicubic"
    )
