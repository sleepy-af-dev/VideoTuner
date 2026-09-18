from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .encoding_utils import (
    IndexerType,
    VapourSynthEnv,
    build_source_index,
    indexer_spec,
    write_vpy_script,
)
from .media import get_assessment_frame_count
from .ssim2_colour import (
    LinearisationMode,
    colour_preamble,
    linearisation_mode,
    missing_colour_tags,
    missing_mastering_display,
)
from .utils import ensure_dir, run_capture

if TYPE_CHECKING:
    from .media import VideoInfo
    from .profiles import Profile
    from .progress import PipelineDisplay


@dataclass(frozen=True)
class SSIM2Result:
    mean: float
    median: float
    p5_low: float
    p95_high: float
    std_dev: float
    count: int


def _calculate_stats_from_scores(scores: list[float]) -> SSIM2Result:
    """Calculate summary statistics from a list of per-frame scores.

    Args:
        scores: List of SSIMULACRA2 scores (one per frame)

    Returns:
        SSIM2Result with calculated statistics

    Raises:
        RuntimeError: If scores list is empty
    """
    if not scores:
        raise RuntimeError("No scores provided for statistics calculation")

    scores_sorted = sorted(scores)
    count = len(scores)
    mean = statistics.mean(scores)
    median = statistics.median(scores)
    std_dev = statistics.stdev(scores) if count > 1 else 0.0

    # Calculate percentiles (5th and 95th)
    p5_idx = max(0, int(count * 0.05) - 1)
    p95_idx = min(count - 1, int(count * 0.95))
    p5_low = scores_sorted[p5_idx]
    p95_high = scores_sorted[p95_idx]

    return SSIM2Result(
        mean=mean,
        median=median,
        p5_low=p5_low,
        p95_high=p95_high,
        std_dev=std_dev,
        count=count,
    )


#: Emitted into the .vpy only when the colour chain actually calls vs-tonemap.
_TONEMAP_GUARD = """
    if not hasattr(core, 'tonemap'):
        print(
            "ERROR: vs-tonemap plugin not loaded; an HDR input cannot be "
            "tonemapped for SSIMULACRA2. Run tools/update_vs.py to install it.",
            file=sys.stderr, flush=True,
        )
        sys.exit(1)
"""


def _build_ssim2_script(
    *,
    ref_path: Path,
    dis_path: Path,
    ref_cache: Path,
    dis_cache: Path,
    preamble: str,
    indexer: IndexerType,
) -> str:
    """Build the VapourSynth script that scores SSIMULACRA2.

    Args:
        ref_path: Reference video path.
        dis_path: Distorted video path.
        ref_cache: Index file for the reference.
        dis_cache: Index file for the distorted encode.
        preamble: Source defining ``prepare(clip)``, from ``colour_preamble``.
            Whether it calls vs-tonemap also decides whether the script
            guards on that plugin being loaded.
        indexer: Source filter to load both clips with.

    Returns:
        The .vpy contents.
    """
    # Gate the vs-tonemap guard on what the preamble actually emitted: only the
    # PQ and HLG chains call the plugin, and an SDR run must not demand one it
    # never touches. Without the guard a missing plugin raises AttributeError,
    # which is swallowed upstream, so the SSIMULACRA2 column simply comes back
    # empty with nothing said on the terminal.
    tonemap_guard = _TONEMAP_GUARD if "core.tonemap." in preamble else ""

    other = next(member for member in IndexerType if member is not indexer)
    display_name = indexer_spec(indexer).display_name

    return f"""import vapoursynth as vs
import sys

core = vs.core

{preamble}

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Only a failure to open a file can be answered by switching indexers, so the
# hint is scoped to the loads. Everything after them - the colour chain, the
# tone curve, the metric - fails for reasons no indexer change can fix.
try:
    print("[vszip] Loading reference video...", file=sys.stderr, flush=True)
    # Load reference and distorted videos
    ref = {indexer_spec(indexer).source_expr(str(ref_path.resolve()), str(ref_cache))}
    print(f"[vszip] Reference: {{ref.width}}x{{ref.height}}, {{ref.format.name}}, {{len(ref)}} frames", file=sys.stderr, flush=True)

    print("[vszip] Loading distorted video...", file=sys.stderr, flush=True)
    dis = {indexer_spec(indexer).source_expr(str(dis_path.resolve()), str(dis_cache))}
    print(f"[vszip] Distorted: {{dis.width}}x{{dis.height}}, {{dis.format.name}}, {{len(dis)}} frames", file=sys.stderr, flush=True)
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr, flush=True)
    print(
        "HINT: this run used the {display_name} indexer."
        " If the input cannot be read, try --indexer {other.value}",
        file=sys.stderr, flush=True,
    )
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    # Validate dimensions match
    if ref.width != dis.width or ref.height != dis.height:
        print(f"ERROR: Resolution mismatch - ref={{ref.width}}x{{ref.height}}, dis={{dis.width}}x{{dis.height}}", file=sys.stderr, flush=True)
        sys.exit(1)

    # Check if vszip plugin is available
    if not hasattr(core, 'vszip'):
        print("ERROR: vszip plugin not loaded", file=sys.stderr, flush=True)
        sys.exit(1)
{tonemap_guard}
    print("[vszip] Converting to linear light...", file=sys.stderr, flush=True)
    # vszip mishandles colour: it forces sRGB on the transfer and ignores the
    # primaries. Both of its conversion paths early-return on a linear RGBS
    # clip, so the conversion is done here and vszip is handed the result.
    ref = prepare(ref)
    dis = prepare(dis)

    print("[vszip] Running SSIMULACRA2 comparison...", file=sys.stderr, flush=True)
    result = ref.vszip.SSIMULACRA2(dis)

    total_frames = len(result)
    print(f"[vszip] Processing {{total_frames}} frames", file=sys.stderr, flush=True)

    if total_frames == 0:
        print("ERROR: SSIMULACRA2 returned empty result", file=sys.stderr, flush=True)
        sys.exit(1)

    # Iterate frames and collect scores.
    # frames() defaults prefetch to the core's thread count and backlog to three
    # times that, and every in-flight frame is an RGBS clip at source resolution,
    # about 95 MB at 4K. Measured on a 4K PQ pair on 32 threads: uncapped peaked
    # at 19.2 GiB of working set, prefetch=8 at 13.3 GiB for 1.2x the wall clock,
    # prefetch=4 at 10.4 GiB for 2.2x. 8 is the knee, and the cap only binds
    # above 8 cores, which is where the memory grows. close=True releases each
    # frame as the loop advances.
    for i, frame in enumerate(result.frames(prefetch=8, close=True)):
        score = frame.props.get("SSIMULACRA2", None)
        if score is not None:
            # Print score to stdout for parsing
            print(f"SCORE:{{score}}", flush=True)
        else:
            print(f"WARNING: Frame {{i}} has no SSIMULACRA2 property", file=sys.stderr, flush=True)
        # Progress to stderr for line_handler
        print(f"vszip progress: {{i + 1}}/{{total_frames}}", file=sys.stderr, flush=True)

    print("[vszip] Assessment complete", file=sys.stderr, flush=True)

except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr, flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""  # noqa: E501  # TODO(E501): shorten line


def _vszip_cache_paths(temp_dir: Path, indexer: IndexerType) -> tuple[Path, Path]:
    """Cache paths for the two vszip inputs, named by the indexer's suffix.

    Shared by ``_run_vszip_assessment`` and ``assess_ssim2_concatenated`` so
    the index built ahead of time and the one the assessment script opens
    are always the same file.

    Args:
        temp_dir: Directory the caches live in.
        indexer: Selected source filter.

    Returns:
        The reference and distorted cache paths.
    """
    suffix = indexer_spec(indexer).index_suffix
    return temp_dir / f"vszip_ref{suffix}", temp_dir / f"vszip_dis{suffix}"


def _run_vszip_assessment(
    *,
    ref_path: Path,
    dis_path: Path,
    vs_env: VapourSynthEnv,
    temp_dir: Path,
    info: VideoInfo,
    indexer: IndexerType,
    line_handler: Callable[[str], bool] | None = None,
) -> SSIM2Result:
    """Run SSIMULACRA2 assessment via vszip VapourSynth plugin.

    Creates a VapourSynth script that uses vszip.SSIMULACRA2 to compute
    per-frame scores, then calculates summary statistics.

    Args:
        ref_path: Reference video path
        dis_path: Distorted video path
        vs_env: VapourSynth environment configuration
        temp_dir: Directory for temporary files
        info: Probed metadata for the input, used to pick the colour
            conversion the reference and distorted clips are scored through.
        indexer: Source filter to load both clips with.
        line_handler: Optional callback for progress updates

    Returns:
        SSIM2Result with calculated metrics

    Raises:
        RuntimeError: If vszip execution fails or produces no scores
    """
    log = logging.getLogger(__name__)

    python_exe = vs_env.vs_dir / "python.exe"
    if not python_exe.exists():
        raise FileNotFoundError(f"VapourSynth python.exe not found at: {python_exe}")

    # Create temporary script
    vpy_path = temp_dir / "vszip_ssim2.vpy"

    # Build VapourSynth script that calculates SSIMULACRA2 and prints scores
    ref_cache, dis_cache = _vszip_cache_paths(temp_dir, indexer)

    # Both of these read the input's probe, while prepare() runs on the
    # reference and distorted encodes. Those agree with the input because the
    # encoder parameter builders write --colorprim/--transfer/--colormatrix
    # from this same VideoInfo, and the lossless reference is built by them
    # too. An encoder that stopped carrying the tags across would break the
    # assumption silently, since the script would then retag against the wrong
    # probe.
    # What these assume is reported once at startup by
    # colour_assumption_notes, since both follow from the probe alone and
    # this function runs once per CRF iteration.
    #
    # A PQ input that declares no mastering display gives BT2390 no peak
    # luminance, and it raises rather than guessing. Assume the BT.2100
    # reference display instead, so the metric is scored rather than dropped.
    # One decision reaches both clips, since prepare() is one function applied
    # to each. HLG needs no equivalent: tonemap.HLG defaults its own peak.
    mode = linearisation_mode(info.color_trc)
    absent = missing_colour_tags(info)
    is_pq = mode is LinearisationMode.PQ
    mastering_missing = is_pq and missing_mastering_display(info)
    preamble = colour_preamble(
        mode, missing=absent, mastering_missing=mastering_missing
    )
    log.info("SSIMULACRA2 colour conversion: %s", mode.value)

    vpy_content = _build_ssim2_script(
        ref_path=ref_path,
        dis_path=dis_path,
        ref_cache=ref_cache,
        dis_cache=dis_cache,
        preamble=preamble,
        indexer=indexer,
    )

    write_vpy_script(vpy_path, vpy_content)
    log.debug("vszip script written to: %s", vpy_path)

    try:
        # Setup environment for VapourSynth portable
        env = vs_env.build_env()

        # Run VapourSynth script
        output = run_capture(
            [str(python_exe), str(vpy_path)],
            cwd=temp_dir,
            env=env,
            line_callback=line_handler,
        )

        # Parse scores from output (lines starting with "SCORE:")
        scores: list[float] = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("SCORE:"):
                with suppress(ValueError):
                    scores.append(float(line[6:]))

        if not scores:
            # Include captured output in error for debugging
            log.error("vszip output:\n%s", output)
            raise RuntimeError("vszip produced no valid SSIMULACRA2 scores")

        return _calculate_stats_from_scores(scores)

    finally:
        # Clean up temporary files
        if vpy_path.exists():
            vpy_path.unlink()
        # Only the distorted index. The reference is one file for the whole CRF
        # search and the largest in the run, so deleting its index here meant
        # rebuilding it on every iteration. What is left behind is one index in
        # the job's own temp folder, which no later job reads.
        if dis_cache.exists():
            dis_cache.unlink()


def assess_ssim2_concatenated(
    reference_path: Path,
    distorted_path: Path,
    workdir: Path,
    temp_dir: Path,
    profile: Profile,
    vs_env: VapourSynthEnv,
    display: PipelineDisplay,
    log: logging.Logger,
    iteration: int,
    info: VideoInfo,
    indexer: IndexerType,
    repo_root: Path,
) -> list[SSIM2Result]:
    """Run SSIMULACRA2 assessment on concatenated reference and distorted files.

    Uses vszip VapourSynth plugin for CPU-based SSIMULACRA2 calculation.

    Args:
        reference_path: Path to concatenated lossless reference
        distorted_path: Path to concatenated distorted encode
        workdir: Working directory for output files
        temp_dir: Temporary directory for intermediate files
        profile: Encoding profile (used for output directory naming)
        vs_env: VapourSynth environment configuration
        display: Progress display manager
        log: Logger instance
        iteration: Current iteration number
        info: Probed metadata for the input, used to pick the colour
            conversion the reference and distorted clips are scored through.
        indexer: Source filter to load both clips with.
        repo_root: Repository root, used to locate the indexer executable
            for the pre-build step. ``workdir`` is a per-job folder under it,
            not the directory ``vapoursynth-portable`` lives in.

    Returns:
        List containing single SSIM2Result for the concatenated comparison
    """
    from .pipeline_types import get_profile_dir

    ssim2_log_path = (
        get_profile_dir(workdir, profile) / f"ssim2_concatenated_iter{iteration}.json"
    )

    # Get frame count for progress tracking
    total_frames = get_assessment_frame_count(reference_path)

    # Build both indexes up front so they get a progress display, rather than
    # letting the source filter build them silently on first decode. The cache
    # names must match what _run_vszip_assessment derives, since a mismatch
    # would build the index twice; _vszip_cache_paths is the one place both
    # sides compute them.
    spec = indexer_spec(indexer)
    ref_cache, dis_cache = _vszip_cache_paths(temp_dir, indexer)
    # The distorted path holds a different encode every iteration, so anything
    # left at its index path describes a file that is gone. ffms2 rejects a
    # mismatched index outright ("The index does not match the source file")
    # rather than rebuilding it, and that failure is swallowed into a dropped
    # metric. The reference index is kept instead: it is built from a file that
    # does not change for the life of the job.
    dis_cache.unlink(missing_ok=True)
    for label, path, cache in (
        ("reference", reference_path, ref_cache),
        ("distorted", distorted_path, dis_cache),
    ):
        if cache.exists():
            continue
        with display.stage(
            f"Indexing {label} ({spec.display_name})",
            total=1,
            show_eta=True,
            transient=True,
            show_done=True,
        ) as index_stage:
            _ = build_source_index(
                source_path=path,
                cache_file=cache,
                indexer=indexer,
                cwd=repo_root,
                line_handler=index_stage.make_index_handler(),
            )

    with display.stage(
        "Running SSIMULACRA2 assessment",
        total=total_frames,
        show_eta=True,
        transient=True,
        show_done=True,
    ) as stage:
        result = _run_vszip_assessment(
            ref_path=reference_path,
            dis_path=distorted_path,
            vs_env=vs_env,
            temp_dir=temp_dir,
            info=info,
            indexer=indexer,
            line_handler=stage.make_vszip_handler(total_frames=total_frames),
        )

    # Write JSON log
    try:
        _ = ensure_dir(ssim2_log_path.parent)
        with open(ssim2_log_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "tool": "vszip",
                    "count": result.count,
                    "mean": result.mean,
                    "median": result.median,
                    "p5_low": result.p5_low,
                    "p95_high": result.p95_high,
                    "std_dev": result.std_dev,
                },
                f,
                indent=2,
            )
    except Exception:
        pass  # Log writing is optional

    log.info(
        "SSIMULACRA2: mean=%.2f, median=%.2f, 5%%=%.2f, 95%%=%.2f",
        result.mean,
        result.median,
        result.p5_low,
        result.p95_high,
    )
    return [result]
