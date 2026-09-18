from __future__ import annotations

import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .constants import (
    MIN_CROP_FRACTION,
    RESOLUTION_CONFIDENCE_THRESHOLD,
    RESOLUTION_RELATIVE_TOLERANCE,
    VMAF_HFR_FPS_THRESHOLD,
    VMAF_MAX_THREADS,
    VMAF_THREAD_CPU_FRACTION,
)
from .encoding_utils import (
    IndexerType,
    VapourSynthEnv,
    indexer_spec,
    is_hdr_video,
    resolve_absolute_path,
    run_vspipe_encode,
    write_vpy_script,
)
from .media import (
    VideoInfo,
    get_bit_depth_from_pix_fmt,
    parse_video_info,
)
from .metric_colour import linearisation_mode
from .tool_parsers import get_float
from .utils import make_relative_path, warn_user
from .vmaf_script import build_vmaf_vpy_script
from .vmaf_types import VmafFamily, VmafModelChoice

if TYPE_CHECKING:
    from .profiles import Profile
    from .progress import PipelineDisplay


def get_cpu_count() -> int:
    """Get number of CPU cores, with fallback to 8 if detection fails."""
    try:
        return os.cpu_count() or 8
    except Exception:
        return 8


@dataclass(frozen=True)
class VMAFResult:
    mean: float
    harmonic_mean: float
    minimum: float
    p1_low: float
    frame_scores: Sequence[float] = field(default_factory=tuple)


def needs_tonemap(info: VideoInfo) -> bool:
    """Check whether a source has to be tonemapped before it can be scored.

    Keyed on the transfer characteristic rather than the primaries. Tone mapping
    compresses a high dynamic range into a low one, which is a question about the
    transfer; a BT.2020 source carrying an SDR transfer is merely wide gamut and
    needs a gamut conversion instead.

    Args:
        info: Parsed metadata for the source.

    Returns:
        True for a PQ or HLG source.
    """
    return is_hdr_video(info.color_trc)


@dataclass(frozen=True)
class ResolutionResult:
    """
    Result of commercial resolution classification.

    Attributes:
        width: Actual video width in pixels
        height: Actual video height in pixels
        base_label: Resolution tier without scan type (e.g., "1080", "720")
        base_height: Standard height for this tier (e.g., 1080 for 1080p)
        confidence: Classification confidence from 0.0 to 1.0
        notes: Human-readable description of the match
        extras: Additional metadata (aspect ratios, error metrics, etc.)
    """

    width: int
    height: int
    base_label: str
    base_height: int
    confidence: float
    notes: str
    extras: dict[str, object] = field(default_factory=dict)


class CommercialResolutionInfer:
    """
    Classify arbitrary pixel dimensions into standard commercial resolution tiers:
        480p, 576p, 720p, 1080p, 1440p, 2160p, 4320p, 8640p

    Handles multiple aspect ratios and orientations:
    - 16:9 widescreen (1920x1080, 1280x720, etc.)
    - 4:3 classic TV/Academy ratio (960x720, 640x480, etc.)
    - 2.39:1 / 2.35:1 cinemascope (letterboxed)
    - 21:9 ultra-wide
    - Portrait/vertical video

    Returns the base resolution tier and confidence score (0.0-1.0).
    """

    # Standard base tiers: (label, base_height, canonical width)
    # Includes both 16:9 (widescreen) and 4:3 (classic/academy) variants
    BASES: list[tuple[str, int, int]] = [
        ("480", 480, 854),  # 16:9 SD
        ("480", 480, 640),  # 4:3 SD
        ("576", 576, 1024),  # 16:9 PAL
        ("576", 576, 768),  # 4:3 PAL
        ("720", 720, 1280),  # 16:9 HD
        ("720", 720, 960),  # 4:3 HD (pillarboxed from 1080p)
        ("1080", 1080, 1920),  # 16:9 Full HD
        ("1080", 1080, 1440),  # 4:3 Full HD (pillarboxed)
        ("1440", 1440, 2560),  # 16:9 QHD
        ("1440", 1440, 1920),  # 4:3 QHD (pillarboxed)
        ("2160", 2160, 3840),  # 16:9 4K/UHD
        ("2160", 2160, 2880),  # 4:3 4K (pillarboxed)
        ("4320", 4320, 7680),  # 16:9 8K
        ("4320", 4320, 5760),  # 4:3 8K (pillarboxed)
        ("8640", 8640, 15360),  # 16:9 16K
        ("8640", 8640, 11520),  # 4:3 16K (pillarboxed)
    ]

    # Absolute pixel tolerance for rounding errors
    ABS_TOL: int = 8

    @classmethod
    def infer(cls, width: int, height: int) -> ResolutionResult:
        """
        Infer commercial resolution from pixel dimensions.

        Tries both (w,h) and swapped (h,w) to handle portrait videos,
        returning whichever has higher confidence.

        Args:
            width: Video width in pixels
            height: Video height in pixels

        Returns:
            ResolutionResult with classification and confidence
        """
        cand1 = cls._infer_one(width, height)
        cand2 = cls._infer_one(height, width)
        return cand1 if cand1.confidence >= cand2.confidence else cand2

    @classmethod
    def _infer_one(cls, w: int, h: int) -> ResolutionResult:
        """Internal: classify a single orientation (w,h)."""
        if h == 0:
            # Prevent division by zero
            return ResolutionResult(
                width=w,
                height=h,
                base_label="480",
                base_height=480,
                confidence=0.0,
                notes="Invalid dimensions (height=0)",
                extras={},
            )

        best = None
        best_err = float("inf")

        for label, bh, bw in cls.BASES:
            base_ar = bw / bh
            obs_ar = w / h

            # tolerance helpers
            def tol(b: float) -> float:
                return max(cls.ABS_TOL, RESOLUTION_RELATIVE_TOLERANCE * b)

            # measure relative differences
            dw = abs(w - bw) / bw if bw > 0 else float("inf")
            dh = abs(h - bh) / bh if bh > 0 else float("inf")
            ar_err = abs(obs_ar - base_ar) / base_ar if base_ar > 0 else float("inf")

            # Three hypotheses: full-frame, cropped height (letterbox), cropped width (pillarbox)  # noqa: E501  # TODO(E501): shorten line
            err_full = (dw + dh) / 2 + 0.25 * ar_err

            err_crop_h = None
            if w >= MIN_CROP_FRACTION * bw and h <= bh + tol(bh):
                deficit = max(0.0, (bh - h) / bh) if bh > 0 else 0.0
                err_crop_h = (
                    (abs(w - bw) / bw if bw > 0 else 0.0)
                    + 0.5 * deficit
                    + 0.25 * ar_err
                )

            err_crop_w = None
            if h >= MIN_CROP_FRACTION * bh and w <= bw + tol(bw):
                deficit = max(0.0, (bw - w) / bw) if bw > 0 else 0.0
                err_crop_w = (
                    (abs(h - bh) / bh if bh > 0 else 0.0)
                    + 0.5 * deficit
                    + 0.25 * ar_err
                )

            for err in (err_full, err_crop_h, err_crop_w):
                if err is None:
                    continue
                if err < best_err:
                    best_err = err
                    best = ResolutionResult(
                        width=w,
                        height=h,
                        base_label=label,
                        base_height=bh,
                        confidence=cls._err_to_conf(err),
                        notes=f"Matched to {label}p (base {bw}x{bh})",
                        extras={
                            "observed_ar": round(obs_ar, 5),
                            "base_ar": round(base_ar, 5),
                            "norm_error": round(err, 5),
                        },
                    )

                    # Early termination for excellent matches
                    if err < 0.01:
                        return best

        # Fallback if no match found (shouldn't happen)
        if best is None:
            label, bh, bw = min(cls.BASES, key=lambda b: abs(h - b[1]))
            best = ResolutionResult(
                width=w,
                height=h,
                base_label=label,
                base_height=bh,
                confidence=0.3,
                notes="Fallback by nearest base height",
                extras={"observed_ar": round(w / h, 5), "base_ar": round(bw / bh, 5)},
            )

        return best

    @staticmethod
    def _err_to_conf(err: float) -> float:
        """Map normalized error to confidence 0..1."""
        err = max(0.0, err)
        conf = 1.0 / (1.0 + 10.0 * err)
        return round(max(0.0, min(1.0, conf)), 3)


def validate_comparison(ref_info: VideoInfo, dis_info: VideoInfo) -> None:
    """Validate that the comparison is supported.

    Raises:
        ValueError: If validation fails
    """
    # Check for upscaling (distorted > reference)
    if ref_info.width and dis_info.width and dis_info.width > ref_info.width:
        raise ValueError(
            f"Upscaling not supported: distorted width ({dis_info.width}) > reference width ({ref_info.width})"  # noqa: E501  # TODO(E501): shorten line
        )
    if ref_info.height and dis_info.height and dis_info.height > ref_info.height:
        raise ValueError(
            f"Upscaling not supported: distorted height ({dis_info.height}) > reference height ({ref_info.height})"  # noqa: E501  # TODO(E501): shorten line
        )

    # Check for color space upgrade (reference is BT.709 but distorted is BT.2020)
    ref_is_2020 = needs_tonemap(ref_info)
    dis_is_2020 = needs_tonemap(dis_info)
    if not ref_is_2020 and dis_is_2020:
        raise ValueError(
            "Color space upgrade not supported: reference is BT.709 but distorted is BT.2020"  # noqa: E501  # TODO(E501): shorten line
        )

    # Check for bit depth upgrade (distorted > reference)
    ref_depth = get_bit_depth_from_pix_fmt(ref_info.pix_fmt)
    dis_depth = get_bit_depth_from_pix_fmt(dis_info.pix_fmt)
    if dis_depth > ref_depth:
        raise ValueError(
            f"Bit depth upgrade not supported: distorted bit depth ({dis_depth}-bit) > reference bit depth ({ref_depth}-bit)"  # noqa: E501  # TODO(E501): shorten line
        )


def select_vmaf_model(
    width: int, height: int, *, fps: float | None = None, use_v0: bool = False
) -> str:
    """Select the VMAF model for a comparison.

    Models are named for a display and a viewing distance rather than a source
    resolution, so the resolution tier only answers the 2160-or-not question.

    Args:
        width: Video width in pixels
        height: Video height in pixels
        fps: Frame rate of the source, used to pick a high frame rate variant
        use_v0: Return the legacy v0 models instead, which have no high frame
            rate variants and so ignore ``fps``

    Returns:
        Model version string (e.g. ``"vmaf_v1.0.16_3d0h"``)
    """
    log = logging.getLogger(__name__)

    # Classify resolution using smart detection
    result = CommercialResolutionInfer.infer(width, height)

    # Log classification details
    log.debug(
        f"Resolution classification: {width}x{height} → {result.base_label}p (confidence: {result.confidence}, AR: {result.extras.get('observed_ar', 'N/A')})"  # noqa: E501  # TODO(E501): shorten line
    )

    # Warn on low confidence matches
    if result.confidence < RESOLUTION_CONFIDENCE_THRESHOLD:
        log.warning(
            f"Low confidence resolution classification for {width}x{height}: {result.notes}",  # noqa: E501  # TODO(E501): shorten line
        )

    is_uhd = result.base_height >= 2160

    if use_v0:
        return "vmaf_4k_v0.6.1" if is_uhd else "vmaf_v0.6.1"

    # hfr is an infix rather than a suffix: vmaf_v1.0.16_hfr_1d5h_2160
    hfr = "hfr_" if fps is not None and fps >= VMAF_HFR_FPS_THRESHOLD else ""
    return f"vmaf_v1.0.16_{hfr}1d5h_2160" if is_uhd else f"vmaf_v1.0.16_{hfr}3d0h"


# ffmpeg reports an unknown model from inside the filtergraph and exits 127:
#   [Parsed_libvmaf_2 @ 0000...] could not load libvmaf model with version: <name>
_VMAF_MODEL_LOAD_FAILURE = re.compile(
    r"could not load libvmaf model with version:\s*(\S+)"
)


def vmaf_model_load_error(output_line: str) -> str | None:
    """Spot a model that libvmaf could not load, in a line of ffmpeg output.

    Args:
        output_line: One line of ffmpeg's output.

    Returns:
        The model name that failed, or None if the line says nothing about it.
    """
    match = _VMAF_MODEL_LOAD_FAILURE.search(output_line)
    return match.group(1) if match else None


def family_of_vmaf_model(model_spec: str) -> VmafFamily:
    """Read the model generation off a model name.

    Args:
        model_spec: A model version name, or a path to a model file.

    Returns:
        V1 for a v1 built-in. Anything else reads as V0, including a custom
        model file, since only the built-ins carry the v1 feature set.
    """
    return VmafFamily.V1 if "v1.0" in model_spec.lower() else VmafFamily.V0


def resolve_vmaf_choice(
    requested_model: str | None, *, use_v0: bool, v1_available: bool
) -> VmafModelChoice:
    """Settle which model generation a run will use.

    Args:
        requested_model: Model named on the command line, if any.
        use_v0: Whether ``--v0-vmaf`` was given.
        v1_available: Result of the startup probe. Callers skip the probe and
            pass False when it cannot change the answer.

    Returns:
        The resolved choice.
    """
    if requested_model is not None:
        family = family_of_vmaf_model(requested_model)
        if use_v0 and family is VmafFamily.V1:
            raise ValueError(
                f"--v0-vmaf conflicts with --vmaf-model {requested_model}, which "
                + "is a v1 model. Drop one of them rather than having the tool "
                + "choose."
            )
        # A named model is used as given. The probe cannot improve on that, and
        # substituting something else would answer a question nobody asked.
        return VmafModelChoice(family=family, explicit_model=requested_model)

    if use_v0:
        return VmafModelChoice(family=VmafFamily.V0)

    if v1_available:
        return VmafModelChoice(family=VmafFamily.V1)

    return VmafModelChoice(family=VmafFamily.V0, fell_back=True)


# 640x480 clears every model's minimum. Two independent floors apply, and both
# fail by reporting a false negative that would silently downgrade every run:
# CAMBI needs 216 in both dimensions, and SpEED chroma's floor varies by model,
# with the 4K@3H model needing 640x480. Measured cost about 0.12s.
VMAF_PROBE_SIZE = "640x480"

# Any v1 name answers the question, since all eight ship in the same libvmaf.
VMAF_PROBE_MODEL = "vmaf_v1.0.16_3d0h"

VMAF_PROBE_TIMEOUT_SECONDS = 30


@lru_cache(maxsize=1)
def vmaf_v1_available(ffmpeg_bin: str = "ffmpeg") -> bool:
    """Probe whether the linked libvmaf carries the v1 models.

    FFmpeg holds no model list and passes the name through as an opaque string,
    so the only way to tell is to score a trivial pair and read the exit code.
    Cached for the process: it must never run per CRF iteration.

    Args:
        ffmpeg_bin: Path to the ffmpeg binary.

    Returns:
        True if a v1 model loaded and scored.
    """
    log = logging.getLogger(__name__)
    src = f"testsrc2=s={VMAF_PROBE_SIZE}:r=5:d=0.4"
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        src,
        "-f",
        "lavfi",
        "-i",
        src,
        "-lavfi",
        "[0:v]format=yuv420p10le[a];[1:v]format=yuv420p10le[b];"
        + f"[b][a]libvmaf=model=version={VMAF_PROBE_MODEL}",
        "-f",
        "null",
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=VMAF_PROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        warn_user(
            log, "ffmpeg not found at '%s'; assuming no VMAF v1 support", ffmpeg_bin
        )
        return False
    except subprocess.TimeoutExpired:
        warn_user(log, "VMAF v1 probe timed out; assuming no VMAF v1 support")
        return False

    # The exit code, not the message: a v1 name on an older libvmaf fails
    # differently from a name that does not exist at all.
    return result.returncode == 0


# libvmaf takes model-config overrides after the model name, separated by a
# colon. That is also -filter_complex's own option delimiter, so it has to reach
# FFmpeg escaped, and the escaping survives two layers of parsing: FFmpeg needs
# to receive TWO literal backslashes.
#
# Measured against ffmpeg, scoring banding-prone content:
#   bare colon        Error applying option 'cambi.enc_bitdepth'
#   one backslash     Error applying option 'cambi.enc_bitdepth'
#   two backslashes   works; score moves 47.19 -> 55.03 at enc_bitdepth=8
#   pipe separator    exits 0, silently swallows every later option including
#                     log_path, so no log is written and the run looks healthy
#
# Note this is a different count from _escape_filter_path() in run_vmaf, which
# escapes a drive-letter colon with one backslash. They are not interchangeable.
_MODEL_OVERRIDE_SEPARATOR = "\\\\" + ":"


def build_libvmaf_model_option(
    model_spec: str, *, enc_bitdepth: int | None = None
) -> str:
    """Build the ``model=`` option for the libvmaf filter.

    Args:
        model_spec: A built-in model version name.
        enc_bitdepth: Bit depth the distorted content was *encoded* at, passed
            to CAMBI when it differs from the depth being measured. CAMBI scales
            its banding detection by the encode depth, so measuring an 8-bit
            encode at 10-bit without this makes it assume 10 and mis-scale.

    Returns:
        The option string, with any override escaped for the filtergraph.
    """
    option = f"model=version={model_spec}"
    if enc_bitdepth is not None:
        option += f"{_MODEL_OVERRIDE_SEPARATOR}cambi.enc_bitdepth={enc_bitdepth}"
    return option


def build_vmaf_filter(
    *,
    width: int,
    height: int,
    dis_bit_depth: int = 8,
    use_v0: bool = False,
) -> str:
    """Build the filter graph ffmpeg applies to the piped tracks.

    Both tracks arrive down one pipe, already tonemapped and already in
    BT.709 SDR, so this does no colour work. It scales a downscaled encode
    back to reference resolution, which deliberately stays here: measured,
    changing only the resampler moved VMAF by 1.78 points, so doing it in
    VapourSynth would tangle a resampling change into a tonemapping one.

    Args:
        width: Reference width, which the distorted track is scaled to.
        height: Reference height.
        dis_bit_depth: Bit depth of the distorted encode, read only by v0.
        use_v0: Measure at the encode's depth rather than at 10-bit.

    Returns:
        The filter graph up to and including the libvmaf input pads.
    """
    # v1 is measured at 10-bit whatever the encode depth, so that banding is
    # present in the signal CAMBI reads. v0 kept the measurement depth matched
    # to the encode, and --v0-vmaf reverts to that so an A/B compares like with
    # like rather than a third thing matching neither generation.
    if use_v0:
        pix_fmt = "yuv420p10le" if dis_bit_depth == 10 else "yuv420p"
    else:
        pix_fmt = "yuv420p10le"
    final = f"format={pix_fmt},setpts=PTS-STARTPTS,settb=AVTB"

    dis_chain = f"[0:v:1]scale={width}:{height}:flags=bicubic,{final}[dis]"
    ref_chain = f"[0:v:0]{final}[ref]"
    return f"{dis_chain};{ref_chain};[dis][ref]"


def run_vmaf(
    ffmpeg_bin: str,
    ref_script: str,
    dis_path: Path,
    ref_info: VideoInfo,
    dis_info: VideoInfo,
    model_spec: str | None = None,
    log_path: Path | None = None,
    cwd: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
    family: VmafFamily = VmafFamily.V1,
    *,
    indexer: IndexerType,
    vs_env: VapourSynthEnv,
    temp_dir: Path,
) -> VMAFResult:
    log = logging.getLogger(__name__)

    # Validate comparison first
    validate_comparison(ref_info, dis_info)

    if log_path is None:
        raise ValueError("log_path must be provided for VMAF run")

    def _escape_filter_path(p: Path) -> str:
        # Convert to forward slashes and escape the drive letter colon for filter syntax
        s = str(p).replace("\\", "/")
        s = re.sub(r"^([A-Za-z]):/", r"\1\\:/", s)
        return s

    use_v0 = family is VmafFamily.V0

    # Auto-select model based on distorted resolution if not specified
    if model_spec is None:
        if dis_info.width and dis_info.height:
            model_spec = select_vmaf_model(
                dis_info.width, dis_info.height, fps=dis_info.fps, use_v0=use_v0
            )
        else:
            # 1080p at 3 display heights is the generation's default, and the
            # direct successor to v0.6.1, so an unknown size keeps the same
            # viewing assumptions rather than dropping a generation.
            model_spec = "vmaf_v0.6.1" if use_v0 else "vmaf_v1.0.16_3d0h"

    # Use the distorted file's own bit depth for the pixel format libvmaf
    # measures at; the scale target below is the reference's, not this.
    dis_bit_depth = get_bit_depth_from_pix_fmt(dis_info.pix_fmt)

    # The header names the generation, which is settled before the run. Which of
    # the generation's models ran depends on this comparison's resolution and
    # frame rate, and only this says which one it was.
    log.info("VMAF model: %s", model_spec)

    # Build model option: prefer explicit path if it exists; otherwise accept
    # direct 'name=' or 'version=' input; fallback to 'version=<spec>'.
    if Path(model_spec).exists():
        model_opt = f"model=path={_escape_filter_path(Path(make_relative_path(Path(model_spec), cwd)))}"  # noqa: E501  # TODO(E501): shorten line
    else:
        lower = model_spec.lower()
        if (
            lower.startswith("name=")
            or lower.startswith("version=")
            or lower.startswith("path=")
        ):
            model_opt = f"model={model_spec}"
        else:
            # CAMBI scales banding detection by the depth the content was
            # encoded at. v1 measures at 10-bit regardless, so an 8-bit encode
            # needs telling, or CAMBI assumes 10 and mis-scales.
            enc_bitdepth = dis_bit_depth if not use_v0 and dis_bit_depth != 10 else None
            model_opt = build_libvmaf_model_option(
                model_spec, enc_bitdepth=enc_bitdepth
            )
    # The distorted track is what gets scaled (build_vmaf_filter scales
    # [0:v:1] to this size), so the target is the reference's own dimensions,
    # not the distorted encode's - which is usually smaller and would
    # otherwise make the scale a no-op, leaving libvmaf two differently
    # sized tracks when the encode was downscaled.
    target_width = ref_info.width or 3840
    target_height = ref_info.height or 2160

    filter_prefix = build_vmaf_filter(
        width=target_width,
        height=target_height,
        dis_bit_depth=dis_bit_depth,
        use_v0=use_v0,
    )
    # libvmaf writes its JSON with a plain fopen, which is still capped at
    # MAX_PATH (260) on Windows even when long paths are enabled OS-wide, and it
    # treats the failure as non-fatal: ffmpeg still exits 0, just with no log.
    # A job folder nested under a batch folder can exceed that, so libvmaf writes
    # to a short path and the result is moved into place afterwards.
    #
    # The temp dir sits under cwd so the filter still gets a short *relative*
    # path: an absolute one would carry a drive letter, and a `:` inside
    # -filter_complex needs more escaping than the graph parser will accept.
    tmp_log_dir = tempfile.mkdtemp(prefix=".vt_vmaf_", dir=cwd if cwd else None)
    tmp_log = Path(tmp_log_dir) / "vmaf.json"
    log_path_escaped = _escape_filter_path(Path(make_relative_path(tmp_log, cwd)))

    # Calculate thread count based on CPU cores
    cpu_count = get_cpu_count()
    n_threads = min(max(1, int(cpu_count * VMAF_THREAD_CPU_FRACTION)), VMAF_MAX_THREADS)

    # VMAF options with sync parameters and explicit threading
    vmaf_opts = (
        f"libvmaf=shortest=true:ts_sync_mode=nearest:{model_opt}:"
        f"n_threads={n_threads}:log_fmt=json:log_path={log_path_escaped}"
    )
    filter_graph = f"{filter_prefix}{vmaf_opts}"

    # The reference frames are selected from the source by the sampling script
    # and the distorted encode is loaded by the selected indexer. Both are
    # tonemapped by vs-tonemap and piped down one vspipe process as two tracks
    # in a matroska stream; ffmpeg reads them back off pipe:0 as [0:v:0] and
    # [0:v:1].
    #
    # The distorted index lives in temp_dir rather than tmp_log_dir, which this
    # function deletes on every call, but it still names a different encode each
    # iteration, so it is removed once this call is done - the same way
    # ssimulacra2_assessment treats its own. The source indexes the reference
    # reads belong to the job and are built before any encoding.
    vpy_path = Path(tmp_log_dir) / "vmaf.vpy"
    dis_cache = temp_dir / f"vmaf_dis{indexer_spec(indexer).index_suffix}"
    log.info("VMAF colour conversion: %s", linearisation_mode(ref_info.color_trc).value)
    write_vpy_script(
        vpy_path,
        build_vmaf_vpy_script(
            ref_script=ref_script,
            dis_path=dis_path,
            dis_cache=dis_cache,
            indexer=indexer,
            info=ref_info,
        ),
    )
    vspipe_args = [
        str(vs_env.vspipe_bin),
        "-c",
        "mkv",
        str(resolve_absolute_path(vpy_path, cwd)),
        "-",
    ]

    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-y",
        "-stats",
        "-an",  # No audio
        "-sn",  # No subtitles
        "-dn",  # No data streams
        "-i",
        "pipe:0",
        "-filter_complex",
        filter_graph,
        "-f",
        "null",
        "-",
    ]
    log.info("VMAF: %s", " ".join(shlex.quote(c) for c in cmd))

    # run_vspipe_encode() streams rather than captures, so the model-load
    # failure has to be caught as it goes past or the error is reduced to an
    # exit status.
    # A list rather than a rebound local: the write happens inside the callback,
    # which a type checker cannot follow, so a plain str | None reads as never
    # reassigned and the failure branch looks unreachable.
    failed_models: list[str] = []

    def watch(line: str) -> bool:
        found = vmaf_model_load_error(line)
        if found is not None:
            failed_models.append(found)
        return line_handler(line) if line_handler is not None else False

    try:
        try:
            _ = run_vspipe_encode(
                vspipe_args=vspipe_args,
                encoder_args=cmd,
                vs_env=vs_env.build_env(),
                cwd=cwd,
                line_handler=watch,
            )
        except RuntimeError as e:
            if failed_models:
                raise RuntimeError(
                    f"VMAF model '{failed_models[0]}' is not available in this "
                    + "FFmpeg's libvmaf. The v1 models need libvmaf 3.2.0 or "
                    + "newer; upgrading FFmpeg alone may not change which "
                    + "libvmaf it links."
                ) from e
            raise
        if tmp_log.exists():
            _ = shutil.move(str(tmp_log), str(log_path))
        else:
            log.error("libvmaf produced no JSON log at %s", tmp_log)
    finally:
        shutil.rmtree(tmp_log_dir, ignore_errors=True)
        # See the comment where this path is built: it names an encode that is
        # replaced on the next iteration.
        if dis_cache.exists():
            dis_cache.unlink()

    try:
        with open(log_path, encoding="utf-8") as f:
            data = cast(object, json.load(f))
        frame_scores: list[float] = []
        mean: float | None = None
        harmonic_mean: float | None = None
        pooled_minimum: float | None = None
        # Prefer pooled mean if present
        if isinstance(data, dict):
            data_dict = cast(dict[str, object], data)
            if "pooled_metrics" in data_dict and isinstance(
                data_dict["pooled_metrics"], dict
            ):
                pooled_metrics = cast(dict[str, object], data_dict["pooled_metrics"])
                if "vmaf" in pooled_metrics and isinstance(
                    pooled_metrics["vmaf"], dict
                ):
                    vmaf_dict = cast(dict[str, object], pooled_metrics["vmaf"])
                    mean = get_float(vmaf_dict, "mean")
                    harmonic_mean = get_float(vmaf_dict, "harmonic_mean")
                    pooled_minimum = get_float(vmaf_dict, "min")
            elif "aggregate" in data_dict and isinstance(data_dict["aggregate"], dict):
                aggregate = cast(dict[str, object], data_dict["aggregate"])
                mean = get_float(aggregate, "vmaf")
            # Collect per-frame values for min and 1% low (and fallback mean)
            if "frames" in data_dict and isinstance(data_dict["frames"], list):
                frames = cast(list[object], data_dict["frames"])
                for fr in frames:
                    try:
                        if isinstance(fr, dict):
                            fr_dict = cast(dict[str, object], fr)
                            if "metrics" in fr_dict and isinstance(
                                fr_dict["metrics"], dict
                            ):
                                metrics = cast(dict[str, object], fr_dict["metrics"])
                                score = get_float(metrics, "vmaf")
                                if score is not None and not math.isnan(score):
                                    frame_scores.append(score)
                    except Exception:
                        continue
        minimum = float("nan")
        p1_low = float("nan")
        if frame_scores:
            if pooled_minimum is None:
                pooled_minimum = min(frame_scores)
            minimum = float(pooled_minimum)
            k = max(1, math.ceil(0.01 * len(frame_scores)))
            low = sorted(frame_scores)[:k]
            p1_low = sum(low) / len(low)
            if mean is None:
                mean = sum(frame_scores) / len(frame_scores)
            if harmonic_mean is None:
                denom = sum((1.0 / v) for v in frame_scores if v != 0)
                harmonic_mean = (len(frame_scores) / denom) if denom else float("nan")
        if harmonic_mean is None:
            harmonic_mean = float("nan")
        if pooled_minimum is None and not math.isnan(minimum):
            pooled_minimum = minimum
        if mean is None:
            log.warning("Could not locate VMAF metrics in %s", log_path)
            return VMAFResult(
                mean=float("nan"),
                harmonic_mean=float(harmonic_mean),
                minimum=float(pooled_minimum)
                if pooled_minimum is not None
                else float("nan"),
                p1_low=p1_low,
                frame_scores=tuple(frame_scores),
            )
        return VMAFResult(
            mean=float(mean),
            harmonic_mean=float(harmonic_mean),
            minimum=float(pooled_minimum)
            if pooled_minimum is not None
            else float("nan"),
            p1_low=float(p1_low),
            frame_scores=tuple(frame_scores),
        )
    except FileNotFoundError as e:
        log.error(
            "Failed to parse VMAF JSON (%s): File not found. Error: %s", log_path, e
        )

        return VMAFResult(
            mean=float("nan"),
            harmonic_mean=float("nan"),
            minimum=float("nan"),
            p1_low=float("nan"),
            frame_scores=tuple(),
        )
    except Exception as e:
        log.warning("Failed to parse VMAF JSON (%s): %s", log_path, e)

        return VMAFResult(
            mean=float("nan"),
            harmonic_mean=float("nan"),
            minimum=float("nan"),
            p1_low=float("nan"),
            frame_scores=tuple(),
        )


def assess_with_vmaf(
    *,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    ref_script: str,
    ref_info: VideoInfo,
    dis_path: Path,
    model_spec: str | None = None,
    log_path: Path,
    cwd: Path | None = None,
    line_handler: Callable[[str], bool] | None = None,
    family: VmafFamily = VmafFamily.V1,
    indexer: IndexerType,
    vs_env: VapourSynthEnv,
    temp_dir: Path,
) -> VMAFResult:
    """High-level helper that probes the encode and runs VMAF.

    Centralizes all VMAF-related orchestration here (probing, filtering, parsing).
    If model_spec is None, automatically selects the appropriate model based on
    the distorted video's resolution. The reference is described by ``ref_info``
    rather than probed, since it is the sampled source and never a file.
    """  # noqa: E501  # TODO(E501): shorten line
    dis_info = parse_video_info(
        dis_path, ffprobe_bin=ffprobe_bin, log_hdr_metadata=False
    )
    return run_vmaf(
        ffmpeg_bin=ffmpeg_bin,
        ref_script=ref_script,
        dis_path=dis_path,
        ref_info=ref_info,
        dis_info=dis_info,
        model_spec=model_spec,
        log_path=log_path,
        cwd=cwd,
        line_handler=line_handler,
        family=family,
        indexer=indexer,
        vs_env=vs_env,
        temp_dir=temp_dir,
    )


def assess_vmaf_concatenated(
    reference_script: str,
    reference_info: VideoInfo,
    reference_frames: int,
    distorted_path: Path,
    workdir: Path,
    repo_root: Path,
    profile: Profile,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    vmaf_model: str | None,
    display: PipelineDisplay,
    log: logging.Logger,
    iteration: int,
    indexer: IndexerType,
    vs_env: VapourSynthEnv,
    temp_dir: Path,
    family: VmafFamily = VmafFamily.V1,
) -> list[VMAFResult]:
    """Run VMAF assessment on the sampled source and a distorted encode.

    Args:
        reference_script: Sampling script that selects the reference frames
            from the source, the same one the encoder is fed.
        reference_info: What that script's output looks like, after any crop.
            The distorted track is scaled to its dimensions.
        reference_frames: How many frames that script yields, for progress.
        distorted_path: Path to concatenated distorted encode
        workdir: Working directory for output files
        repo_root: Repository root directory
        profile: Encoding profile (used for output directory organization)
        ffmpeg_bin: Path to ffmpeg binary
        ffprobe_bin: Path to ffprobe binary
        vmaf_model: VMAF model to use (None for auto-selection)
        display: Progress display manager
        log: Logger instance
        iteration: Current iteration number
        indexer: Source filter to load the distorted encode with.
        vs_env: VapourSynth environment configuration.
        temp_dir: Directory for intermediate files, where the distorted
            encode's index is written.

    Returns:
        List containing single VMAFResult for the concatenated comparison
    """
    from .pipeline_types import get_profile_dir

    vmaf_log_path = (
        get_profile_dir(workdir, profile) / f"vmaf_concatenated_iter{iteration}.json"
    )

    with display.stage(
        "Running VMAF assessment",
        total=reference_frames,
        show_eta=True,
        transient=True,
        show_done=True,
    ) as stage:
        result = assess_with_vmaf(
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            ref_script=reference_script,
            ref_info=reference_info,
            dis_path=distorted_path,
            model_spec=vmaf_model,
            log_path=vmaf_log_path,
            cwd=repo_root,
            line_handler=stage.make_ffmpeg_handler(total_frames=reference_frames),
            family=family,
            indexer=indexer,
            vs_env=vs_env,
            temp_dir=temp_dir,
        )

    log.info(
        "VMAF: mean=%.2f, harmonic_mean=%.2f, 1%%=%.2f, min=%.2f",
        result.mean,
        result.harmonic_mean,
        result.p1_low,
        result.minimum,
    )
    return [result]
