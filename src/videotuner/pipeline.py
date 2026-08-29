from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .budget_search import select_within_budget
from .constants import (
    CRF_SEARCH_MAX_ITERATIONS,
    METRIC_DECIMALS,
)
from .create_encodes import (
    build_ffms2_index,
    calculate_cropdetect_values,
)
from .crf_search import (
    CRFFloorError,
    CRFSearchState,
    QualityTarget,
)
from .encoding_utils import (
    CropValues,
    SampledSource,
    UsableRange,
    combine_crop_values,
    is_hdr_video,
)
from .media import (
    InvalidVideoFileError,
    combine_video_info,
    get_frame_count,
    parse_video_info,
)
from .pipeline_cli import (
    PipelineArgs,
    build_arg_parser,
    parse_cli,
    validate_args,
)
from .pipeline_display import (
    check_and_display_bitrate_warning,
    display_assessment_summary,
    display_best_within_budget,
    display_ignored_args_warnings,
    display_multi_profile_results,
    display_settings_summary,
    format_bitrate_percentage,
)
from .pipeline_iteration import run_single_bitrate_iteration, run_single_crf_iteration
from .pipeline_multi_profile import (
    MultiProfileSearchParams,
    rank_profile_results,
    run_multi_profile_search,
)
from .pipeline_reference import (
    MetricSamplingParams,
    are_sampling_params_equal,
    generate_metric_reference,
)
from .pipeline_types import (
    BudgetPoint,
    IterationContext,
    JobResult,
    get_reference_dir,
)
from .pipeline_validation import (
    build_targets,
    check_sources_compatible,
    has_targets,
    validate_sampling_parameters,
)
from .profiles import Profile
from .progress import PipelineDisplay, TranscriptFormatter
from .ssimulacra2_assessment import SSIM2Result
from .utils import (
    configure_logging,
    display_path,
    ensure_dir,
    get_app_root,
    log_section,
    resolve_run_folder,
    sanitize_filename,
)
from .vmaf_assessment import VMAFResult


def run_pipeline(
    args: PipelineArgs, *, show_title: bool = True, job_folder: Path | None = None
) -> JobResult:
    """Run one job, guaranteeing its log handler is detached afterwards.

    ``show_title`` is False for jobs inside a batch, which prints the banner
    once at the start and a ``[N/M]`` header per job instead.

    The body attaches a FileHandler to the root logger for this job's log file.
    Leaving it attached would make every later job in a batch write into this
    job's log as well, so anything the body added is removed here on every exit
    path.
    """
    root = logging.getLogger()
    pre_existing = set(map(id, root.handlers))
    try:
        return _run_pipeline_body(args, show_title=show_title, job_folder=job_folder)
    finally:
        for handler in list(root.handlers):
            if id(handler) not in pre_existing:
                root.removeHandler(handler)
                handler.close()


def _run_pipeline_body(
    args: PipelineArgs, *, show_title: bool = True, job_folder: Path | None = None
) -> JobResult:
    parser = build_arg_parser()

    # Validate arguments and resolve profiles
    validation = validate_args(args, parser)
    selected_profile = validation.selected_profile
    multi_profile_list = validation.multi_profile_list
    multi_profile_display = validation.multi_profile_display

    crop_detect: bool = bool(args.crop_detect)

    display = PipelineDisplay(show_title=show_title)

    # Configure logging (file-only; console output is driven by the Rich UI)
    level = configure_logging(verbose=bool(args.verbose), quiet=bool(args.quiet))
    log = logging.getLogger(__name__)

    input_path: Path = Path(args.input)
    if not input_path.exists():
        # Not parser.error(): that raises SystemExit, which would kill a batch
        # partway through instead of failing this one job.
        display.console.print(
            f"\n[bold red]Error:[/bold red] Input not found: {input_path}\n"
        )
        log.error("Input not found: %s", input_path)
        return JobResult.failure(input_path, "input not found")

    # input_path names the job; source_paths are the files it reads. They are
    # the same thing unless several files are being read as one source.
    if args.as_one_source:
        from .batch import discover_videos

        source_paths = discover_videos(input_path)
        if not source_paths:
            display.console.print(
                f"\n[bold red]Error:[/bold red] No video files found in {input_path}\n"
            )
            log.error("No video files found in %s", input_path)
            return JobResult.failure(input_path, "no video files found")
    else:
        source_paths = [input_path]

    log.info("Probing input with ffprobe ...")
    ffprobe_bin: str = args.ffprobe_bin
    try:
        infos = [parse_video_info(p, ffprobe_bin=ffprobe_bin) for p in source_paths]
    except InvalidVideoFileError as e:
        display.console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        log.error("Invalid video file: %s", e)
        return JobResult.failure(input_path, "invalid video file")

    if len(source_paths) > 1:
        problems = check_sources_compatible(
            list(zip(source_paths, infos, strict=True)),
            guard_start_percent=max(0.0, args.guard_start_percent),
            guard_end_percent=max(0.0, args.guard_end_percent),
            guard_seconds=max(0.0, args.guard_seconds),
        )
        if problems:
            headline = "These files cannot be read as one source:"
            display.console.print(f"\n[bold red]Error:[/bold red] {headline}\n")
            for problem in problems:
                display.console.print(f"[red]  {problem}[/red]")
            log.error("Incompatible sources: %s", "; ".join(problems))
            return JobResult.failure(input_path, "incompatible sources")

    info = combine_video_info(infos)
    res = f"{info.width}x{info.height}" if (info.width and info.height) else "unknown"
    log.info(
        "Detected fps=%.3f, duration=%.2fs, pix_fmt=%s, res=%s",
        info.fps,
        info.duration,
        info.pix_fmt or "unknown",
        res,
    )

    # Determine app root early for tool detection
    repo_root: Path = get_app_root()

    # The job folder and its log are set up before anything is displayed, so the
    # terminal transcript reaches the log from the first line on. Errors below
    # (HDR/x264, guard bands) used to be raised before the handler existed and
    # so never appeared in any log.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stem = sanitize_filename(input_path.stem)
    if job_folder is not None:
        # A batch has already named this folder and fitted it against the batch
        # folder it sits in, so it is used exactly as given.
        workdir = job_folder
        safe_stem = job_folder.name
    else:
        # --workdir is the parent that run folders go in, never the run folder
        # itself, so every run gets its own timestamped folder and repeat runs
        # cannot overwrite each other.
        jobs_root = args.workdir if args.workdir else repo_root / "jobs"
        slugs = [p.name for p in ([selected_profile] if selected_profile else [])]
        slugs += [p.name for p in multi_profile_list]
        workdir, fitted = resolve_run_folder(jobs_root, safe_stem, timestamp, slugs)
        if fitted != safe_stem:
            note = f"Job folder name shortened to fit the path limit: {fitted}"
            display.console.print(f"[yellow]{note}[/yellow]")
        safe_stem = fitted
    _ = ensure_dir(workdir)

    # Create temp subdirectory for temporary files
    temp_dir = ensure_dir(workdir / "temp")

    # Add file logger (default in job folder). The folder already names the job,
    # so the log does not repeat it: a name repeated inside itself is the only
    # filename that grows with the job name, and it outran the path budget's
    # filename margin on long names. A job folder looks the same inside whether
    # it came from a single run or from a batch.
    if not args.log_file:
        log_file: Path = workdir / "job.log"
    else:
        log_file = Path(args.log_file)
        try:
            _ = ensure_dir(log_file.parent)
        except OSError as e:
            log.warning("Could not create log directory %s: %s", log_file.parent, e)

    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(TranscriptFormatter())
        logging.getLogger().addHandler(fh)

    except Exception as e:
        log.warning("Could not attach file logger at %s: %s", log_file, e)

    log_section(log, "Initialization")

    try:
        log.info("Log file: %s", display_path(log_file, repo_root))
        log.info("Job folder: %s", display_path(workdir, repo_root))
        # Recorded in full because the job folder name may have been shortened.
        log.info("Source: %s", input_path.resolve())
    except OSError as e:
        log.warning("Failed to log file/job folder paths due to OSError: %s", e)

    # Display active settings summary. The console tees this into the log, so
    # there is no second hand-written copy of it here.
    display_settings_summary(
        display.console, args, multi_profile_display, input_path.name
    )

    # Validate HDR + x264 combination (x264 cannot carry HDR10 metadata)
    if is_hdr_video(info.color_trc):
        from .encoder_type import EncoderType as _EncoderType

        x264_profiles: list[str] = []
        if selected_profile and selected_profile.encoder == _EncoderType.X264:
            x264_profiles.append(selected_profile.name)
        for p in multi_profile_list:
            if p.encoder == _EncoderType.X264:
                x264_profiles.append(p.name)
        if x264_profiles:
            profiles_str = ", ".join(x264_profiles)
            msg = (
                f"HDR source detected but x264 encoder cannot carry HDR10 metadata. "
                f"x264 profile(s): {profiles_str}. Use x265 for HDR content."
            )
            display.console.print(f"\n[bold red]Error:[/bold red] {msg}\n")
            log.error(msg)
            return JobResult.failure(input_path, "HDR source with x264 profile")

    # Warn about ignored arguments when using bitrate profiles
    bitrate_profile_names = [p.name for p in multi_profile_list if p.is_bitrate_mode]
    display_ignored_args_warnings(
        display.console,
        bitrate_profile_names=bitrate_profile_names,
        crf_start_value=args.crf_start_value,
        crf_interval=args.crf_interval,
    )

    # Validate window sizes fit within video duration (after guard bands)
    guard_start_percent: float = max(0.0, args.guard_start_percent)
    guard_end_percent: float = max(0.0, args.guard_end_percent)
    guard_seconds: float = max(0.0, args.guard_seconds)
    start_guard_time = max(info.duration * guard_start_percent, guard_seconds)
    end_guard_time = max(info.duration * guard_end_percent, guard_seconds)
    usable_duration = info.duration - (start_guard_time + end_guard_time)

    if usable_duration <= 0:
        msg1 = (
            f"ERROR: Guard bands ({guard_start_percent * 100:.1f}% start, "
            f"{guard_end_percent * 100:.1f}% end, seconds min {guard_seconds:.1f}) "
            f"leave no usable duration in {info.duration:.1f}s video"
        )
        msg2 = "ERROR: Reduce --guard-start-percent, --guard-end-percent, or --guard-seconds"  # noqa: E501  # TODO(E501): shorten line
        display.console.print(f"[bold red]{msg1}[/bold red]")
        display.console.print(f"[bold red]{msg2}[/bold red]")
        log.error(
            "Guard bands (%.1f%% = %.1fs) leave no usable duration in %.1fs video",
            (guard_start_percent + guard_end_percent) * 100,
            start_guard_time + end_guard_time,
            info.duration,
        )
        log.error("Reduce guard settings")
        return JobResult.failure(input_path, "guard bands leave no usable duration")

    # Build FFMS2 index for source (shared across cropdetect and all encodes)
    # This is done BEFORE cropdetect to avoid delays during crop detection
    # Index is stored alongside the source file for persistence across job runs
    cache_files = {p: p.parent / f"{p.stem}.ffindex" for p in source_paths}
    if crop_detect or args.vmaf or args.ssim2:
        for source in source_paths:
            cache_file = cache_files[source]
            # Only build if index doesn't exist
            if not cache_file.exists():
                with display.stage(
                    f"Building FFMS2 Index ({source.name})",
                    total=1,  # Placeholder - updated when ffmsindex reports frames
                    show_eta=True,
                    transient=True,  # Transient to avoid duplicate header line
                    show_done=True,
                ) as index_stage:
                    _ = build_ffms2_index(
                        source_path=source,
                        cache_file=cache_file,
                        cwd=repo_root,
                        line_handler=index_stage.make_ffmsindex_handler(),
                    )
            else:
                display.console.print(
                    f"[cyan]Using Existing FFMS2 Index[/cyan] ({source.name})"
                )

    log_section(log, "Analysis")

    # Calculate cropdetect values once (shared across ALL encodes)
    crop_values: CropValues | None = None

    if crop_detect:
        # Each file is measured on its own and the results combined, taking the
        # most aggressive crop per edge: splicing needs matching dimensions, and
        # cropping more can only remove border, never reinstate content.
        measured: list[CropValues] = []
        for source, source_info in zip(source_paths, infos, strict=True):
            source_frames = get_frame_count(source, source_info)
            # Calculate expected sample count for progress bar
            _skip = max(1, int(source_frames * 0.10))
            _step = max(1, round(source_info.fps * args.cropdetect_interval))
            expected_samples = len(range(_skip, source_frames - _skip, _step))
            log.info(
                "Detecting crop in %s (%d frames, %d samples)...",
                source.name,
                source_frames,
                expected_samples,
            )
            label = "Detecting Crop"
            if len(source_paths) > 1:
                label = f"Detecting Crop ({source.name})"
            with display.stage(
                label,
                total=expected_samples,
                show_eta=True,
                transient=True,
                show_done=True,
            ) as cropdetect_stage:
                measured.append(
                    calculate_cropdetect_values(
                        source_path=source,
                        start_frame=0,
                        num_frames=source_frames,
                        fps=source_info.fps,
                        is_hdr=is_hdr_video(source_info.color_trc),
                        interval=args.cropdetect_interval,
                        ffmpeg_bin=args.ffmpeg_bin,
                        source_width=source_info.width or 0,
                        source_height=source_info.height or 0,
                        cwd=repo_root,
                        line_handler=cropdetect_stage.make_cropdetect_handler(),
                        cropdetect_mode=args.cropdetect_mode,
                        cropdetect_limit=args.cropdetect_limit,
                        cropdetect_round=args.cropdetect_round,
                        cropdetect_mv_threshold=args.cropdetect_mv_threshold,
                        cropdetect_low=args.cropdetect_low,
                        cropdetect_high=args.cropdetect_high,
                    )
                )

        crop_values = combine_crop_values(measured)
        final_width = (info.width or 0) - crop_values.left - crop_values.right
        final_height = (info.height or 0) - crop_values.top - crop_values.bottom
        log.info(
            "CropDetect values: left=%d, top=%d, right=%d, bottom=%d (final: %dx%d)",
            crop_values.left,
            crop_values.top,
            crop_values.right,
            crop_values.bottom,
            final_width,
            final_height,
        )
        log.info("CropDetect will be applied to ALL encoding operations")
    else:
        log.info(
            "CropDetect disabled - analyzing full frame including any letterboxing"
        )

    # ===================================================================
    # PERIODIC SAMPLING APPROACH
    # ===================================================================

    log_section(log, "Sample Selection")

    # Guard bands are a fraction of a file's length, so every file gets its own
    # usable range. Each is sampled on its own and the samples joined, which is
    # what makes every intro and set of credits get skipped rather than only the
    # outermost pair.
    # Note: guard percentages are already in decimal form (0.0-1.0), not 0-100
    guard_start_ratio = args.guard_start_percent
    guard_end_ratio = args.guard_end_percent

    sampled_sources: list[SampledSource] = []
    for source, source_info in zip(source_paths, infos, strict=True):
        source_frames = get_frame_count(source, source_info)
        start = int(guard_start_ratio * source_frames)
        end = source_frames - int(guard_end_ratio * source_frames)
        sampled_sources.append(
            SampledSource(
                path=source,
                cache_file=cache_files[source],
                usable_range=UsableRange(start=start, end=end, frame_count=end - start),
            )
        )

    total_frames = sum(
        get_frame_count(s, i) for s, i in zip(source_paths, infos, strict=True)
    )
    guard_start_frames = sum(s.usable_range.start for s in sampled_sources)
    guard_end_frames = total_frames - sum(s.usable_range.end for s in sampled_sources)

    if len(source_paths) > 1:
        log.info("Reading %d files as one source", len(source_paths))
        for source in source_paths:
            log.info("  %s", source.name)

    log.info(
        "Total frames: %d (%.3f fps × %.1fs)", total_frames, info.fps, info.duration
    )

    # Log HDR metadata if present (logged here after file logger is set up)
    if info.mastering_display_color_primaries or info.maximum_content_light_level:
        hdr_parts: list[str] = []
        if info.mastering_display_color_primaries:
            hdr_parts.append(f"colorspace={info.mastering_display_color_primaries}")
        if info.maximum_content_light_level:
            hdr_parts.append(f"MaxCLL={info.maximum_content_light_level}")
        if info.maximum_frameaverage_light_level:
            hdr_parts.append(f"MaxFALL={info.maximum_frameaverage_light_level}")
        log.info("HDR metadata: %s", ", ".join(hdr_parts))

    log.info(
        "Guard bands: start=%d frames (%.1f%%), end=%d frames (%.1f%%)",
        guard_start_frames,
        guard_start_ratio * 100,
        guard_end_frames,
        guard_end_ratio * 100,
    )

    # Validate periodic sampling parameters for both metrics
    vmaf_valid, ssim2_valid = validate_sampling_parameters(
        args=args,
        total_frames=total_frames,
        guard_start_frames=guard_start_frames,
        guard_end_frames=guard_end_frames,
        log=log,
    )
    args.vmaf = vmaf_valid
    args.ssim2 = ssim2_valid

    # Validate that at least one metric is enabled
    if not args.vmaf and not args.ssim2:
        log.error("No metrics have valid samples - cannot proceed")
        return JobResult.failure(input_path, "no valid samples")

    log_section(log, "Reference Generation")

    # Always use x265 for lossless reference: both x264 (--qp 0) and x265
    # (--lossless) produce visually identical output, but x265 handles HDR
    # content which x264 cannot.
    from .encoder_type import EncoderType

    lossless_encoder = EncoderType.X265

    # Create concatenated reference files for each metric
    lossless_profile = Profile(
        name="lossless",
        description="Lossless reference extraction",
        settings={"preset": "ultrafast"},
        encoder=lossless_encoder,
    )

    vmaf_ref_path: Path | None = None
    ssim2_ref_path: Path | None = None
    reference_dir = get_reference_dir(workdir)

    # Check if we can share samples between metrics (identical sampling parameters)
    sharing_samples = are_sampling_params_equal(args)

    if sharing_samples:
        # Generate ONE shared reference file for both metrics
        log.info(
            "Using shared samples for VMAF and SSIM2 (identical sampling parameters)"
        )
        display.console.print("[cyan]Using shared samples for VMAF and SSIM2[/cyan]")

        shared_sampling = MetricSamplingParams(
            interval_frames=args.vmaf_interval_frames,  # Same as ssim2
            region_frames=args.vmaf_region_frames,  # Same as ssim2
            sources=tuple(sampled_sources),
        )
        shared_ref_path = generate_metric_reference(
            metric_type="shared",
            output_dir=reference_dir,
            sampling_params=shared_sampling,
            fps=info.fps,
            lossless_profile=lossless_profile,
            video_info=info,
            mkvmerge_bin=args.mkvmerge_bin,
            repo_root=repo_root,
            temp_dir=temp_dir,
            display=display,
            log=log,
            crop_detect=crop_detect,
            crop_values=crop_values,
        )
        if shared_ref_path is None:
            args.vmaf = False
            args.ssim2 = False
        else:
            # Point both metrics to the same reference file
            vmaf_ref_path = shared_ref_path
            ssim2_ref_path = shared_ref_path
    else:
        # Generate separate references for each metric (different sampling parameters)
        if args.vmaf:
            vmaf_sampling = MetricSamplingParams(
                interval_frames=args.vmaf_interval_frames,
                region_frames=args.vmaf_region_frames,
                sources=tuple(sampled_sources),
            )
            vmaf_ref_path = generate_metric_reference(
                metric_type="vmaf",
                output_dir=reference_dir,
                sampling_params=vmaf_sampling,
                fps=info.fps,
                lossless_profile=lossless_profile,
                video_info=info,
                mkvmerge_bin=args.mkvmerge_bin,
                repo_root=repo_root,
                temp_dir=temp_dir,
                display=display,
                log=log,
                crop_detect=crop_detect,
                crop_values=crop_values,
            )
            if vmaf_ref_path is None:
                args.vmaf = False

        if args.ssim2:
            ssim2_sampling = MetricSamplingParams(
                interval_frames=args.ssim2_interval_frames,
                region_frames=args.ssim2_region_frames,
                sources=tuple(sampled_sources),
            )
            ssim2_ref_path = generate_metric_reference(
                metric_type="ssim2",
                output_dir=reference_dir,
                sampling_params=ssim2_sampling,
                fps=info.fps,
                lossless_profile=lossless_profile,
                video_info=info,
                mkvmerge_bin=args.mkvmerge_bin,
                repo_root=repo_root,
                temp_dir=temp_dir,
                display=display,
                log=log,
                crop_detect=crop_detect,
                crop_values=crop_values,
            )
            if ssim2_ref_path is None:
                args.ssim2 = False

    # Build targets (needed for any search module)
    has_quality_targets = has_targets(args)
    targets: list[QualityTarget] = build_targets(args) if has_quality_targets else []
    targets_for_display: list[QualityTarget] | None = (
        targets if has_quality_targets else None
    )

    # Initialize result variables
    vmaf_results: list[VMAFResult] = []
    ssim2_results: list[SSIM2Result] = []
    scores: dict[str, float | None] = {}
    # Optimal scores from CRF search (if converged) - used for final display
    # Note: CRF search filters out None values, so optimal scores never contain None
    optimal_search_scores: dict[str, float] | None = None
    optimal_crf: float | None = None
    _predicted_bitrate: float = 0.0
    optimal_predicted_bitrate: float = 0.0
    # Every encode a single-profile CRF search runs, for the budget search
    single_tested_points: list[BudgetPoint] = []

    # This job's summary row. Overwritten by the multi-profile branch with the
    # winning profile; otherwise filled in from the single-profile search below.
    result_profile_name: str | None = (
        selected_profile.name if selected_profile else None
    )
    result_crf: float | None = None
    result_bitrate: float = 0.0

    # ========== ASSESSMENT ONLY MODE ==========
    if args.assessment_only:
        # selected_profile is guaranteed when not in multi-profile search mode
        assert selected_profile is not None
        log.info("\n=== Running Assessment Only ===")
        log.info(
            "%s: %s", selected_profile.display_label, selected_profile.display_name
        )

        # Create iteration context
        ctx = IterationContext(
            input_path=input_path,
            sources=sampled_sources,
            workdir=workdir,
            temp_dir=temp_dir,
            repo_root=repo_root,
            info=info,
            selected_profile=selected_profile,
            total_frames=total_frames,
            guard_start_frames=guard_start_frames,
            guard_end_frames=guard_end_frames,
            vmaf_ref_path=vmaf_ref_path,
            ssim2_ref_path=ssim2_ref_path,
            args=args,
            display=display,
            log=log,
            crop_values=crop_values,
            sharing_samples=sharing_samples,
        )

        # Detect bitrate vs CRF mode
        if selected_profile.is_bitrate_mode:
            # Bitrate mode: determine stats file location if multi-pass
            bitrate_kbps = selected_profile.bitrate or 0
            pass_num = selected_profile.pass_number or 1
            log.info("Bitrate: %d kbps", bitrate_kbps)
            log.info("Pass: %d\n", pass_num)

            # Display console header for assessment-only bitrate mode
            pass_mode = selected_profile.pass_mode_description
            display.console.print()
            display.console.print(
                f"[bold cyan]Encoding at {bitrate_kbps} kbps ({pass_mode})[/bold cyan]"
            )

            # Run bitrate iteration
            (
                scores,
                vmaf_results,
                ssim2_results,
                _predicted_bitrate,
                _vmaf_distorted_path,
                _ssim2_distorted_path,
            ) = run_single_bitrate_iteration(ctx, iteration=1)
        else:
            # CRF mode (existing behavior)
            log.info("CRF: %.1f\n", args.crf_start_value)

            # Display console header for assessment-only CRF mode
            display.console.print()
            display.console.print(
                f"[bold cyan]Encoding at CRF {args.crf_start_value:.1f}[/bold cyan]"
            )

            # Run single iteration
            (
                scores,
                vmaf_results,
                ssim2_results,
                _predicted_bitrate,
                _vmaf_distorted_path,
                _ssim2_distorted_path,
            ) = run_single_crf_iteration(ctx, args.crf_start_value, iteration=1)

        # Note: predicted_bitrate is already calculated inside iteration function

    # ========== CRF SEARCH MODE (default) ==========
    elif not args.multi_profile_search:
        # selected_profile is guaranteed when not in multi-profile search mode
        assert selected_profile is not None
        crf_search_state = CRFSearchState(targets, args.crf_interval)

        # Create iteration context with selected profile (may have been updated by profile search)  # noqa: E501  # TODO(E501): shorten line
        ctx = IterationContext(
            input_path=input_path,
            sources=sampled_sources,
            workdir=workdir,
            temp_dir=temp_dir,
            repo_root=repo_root,
            info=info,
            selected_profile=selected_profile,
            total_frames=total_frames,
            guard_start_frames=guard_start_frames,
            guard_end_frames=guard_end_frames,
            vmaf_ref_path=vmaf_ref_path,
            ssim2_ref_path=ssim2_ref_path,
            args=args,
            display=display,
            log=log,
            crop_values=crop_values,
            sharing_samples=sharing_samples,
        )

        iteration = 0
        current_crf: float = args.crf_start_value
        max_iterations = CRF_SEARCH_MAX_ITERATIONS
        scores = {}
        crf_to_predicted_bitrate_single: dict[
            float, float
        ] = {}  # Track CRF -> predicted bitrate

        log.info("\n=== Starting CRF Search ===")
        log.info(
            "%s: %s", selected_profile.display_label, selected_profile.display_name
        )
        log.info("Targets:")
        for target in targets:
            log.info("  %s >= %.2f", target.metric_name, target.target_value)
        log.info("Starting CRF: %.1f", current_crf)
        log.info("CRF Interval: %.1f\n", args.crf_interval)

        display.console.print()
        display.console.print(
            f"[bold]CRF Search: {selected_profile.display_label} '{selected_profile.display_name}'[/bold]"  # noqa: E501  # TODO(E501): shorten line
        )

        while iteration < max_iterations:
            iteration += 1

            # Display iteration header
            display.console.print()
            display.console.print(
                f"[bold cyan]CRF Iteration {iteration}: CRF {current_crf:.1f}[/bold cyan]"  # noqa: E501  # TODO(E501): shorten line
            )

            log.info("\n=== CRF Iteration %d: CRF %.1f ===", iteration, current_crf)

            # Run encoding and assessment for this CRF
            (
                scores,
                vmaf_results,
                ssim2_results,
                _predicted_bitrate,
                _vmaf_distorted_path,
                _ssim2_distorted_path,
            ) = run_single_crf_iteration(ctx, current_crf, iteration)

            # Store predicted bitrate for this CRF
            crf_to_predicted_bitrate_single[current_crf] = _predicted_bitrate
            single_tested_points.append(
                BudgetPoint(
                    profile_name=selected_profile.name,
                    crf=current_crf,
                    scores=scores,
                    predicted_bitrate_kbps=_predicted_bitrate,
                )
            )

            # Add result to search state
            search_scores = {k: v for k, v in scores.items() if v is not None}
            crf_search_state.add_result(current_crf, search_scores)

            # Display iteration targets summary
            display_assessment_summary(
                display.console,
                scores,
                targets=targets,
                iteration=iteration,
                targets_only=True,
                metric_decimals=args.metric_decimals,
            )

            # Display search status summary
            if crf_search_state.all_targets_met():
                display.console.print("[green]✓ All targets met[/green]")
            else:
                unmet_count = sum(1 for t in targets if not t.is_met())
                display.console.print(f"[red]✗ {unmet_count} target(s) not met[/red]")

            # Log iteration summary
            log.info("Iteration %d results:", iteration)
            for key, value in scores.items():
                if value is not None:
                    log.info("  %s: %.3f", key, value)
            log.info("Targets:")
            for target in targets:
                status = "MET" if target.is_met() else "NOT MET"
                delta_str = (
                    f"(Δ={target.delta():.{METRIC_DECIMALS}f})"
                    if target.delta() is not None
                    else ""
                )
                log.info(
                    f"  %s >= %.{METRIC_DECIMALS}f: %s %s",
                    target.metric_name,
                    target.target_value,
                    status,
                    delta_str,
                )

            # Check for convergence
            if crf_search_state.is_converged():
                optimal_crf = crf_search_state.get_optimal_crf()
                optimal_search_scores = crf_search_state.get_optimal_scores()

                # Get predicted bitrate for the optimal CRF
                if (
                    optimal_crf is not None
                    and optimal_crf in crf_to_predicted_bitrate_single
                ):
                    optimal_predicted_bitrate = crf_to_predicted_bitrate_single[
                        optimal_crf
                    ]
                else:
                    optimal_predicted_bitrate = 0.0

                display.console.print(
                    f"[bold green]CRF Search complete: Optimal CRF = {optimal_crf:.1f}[/bold green]"  # noqa: E501  # TODO(E501): shorten line
                )
                log.info("\nCRF Search complete after %d iterations", iteration)
                log.info("Optimal CRF: %.1f", optimal_crf)
                break

            # Calculate next CRF
            try:
                next_crf = crf_search_state.calculate_next_crf(current_crf)
            except CRFFloorError as e:
                display.console.print(f"[bold red]CRF floor reached: {e}[/bold red]")
                log.warning("CRF floor reached: %s", e)
                return JobResult.failure(input_path, "CRF floor reached")

            if next_crf is None:
                if crf_search_state.all_targets_met():
                    optimal_crf = crf_search_state.get_optimal_crf()
                    optimal_search_scores = crf_search_state.get_optimal_scores()

                    # Get predicted bitrate for the optimal CRF
                    if (
                        optimal_crf is not None
                        and optimal_crf in crf_to_predicted_bitrate_single
                    ):
                        optimal_predicted_bitrate = crf_to_predicted_bitrate_single[
                            optimal_crf
                        ]
                    else:
                        optimal_predicted_bitrate = 0.0

                    display.console.print(
                        f"[bold green]CRF Search complete: Optimal CRF = {optimal_crf:.1f}[/bold green]"  # noqa: E501  # TODO(E501): shorten line
                    )
                    log.info("\nCRF Search complete after %d iterations", iteration)
                    log.info("Optimal CRF: %.1f", optimal_crf)
                else:
                    display.console.print(
                        "[bold yellow]CRF Search exhausted - targets may be unreachable[/bold yellow]"  # noqa: E501  # TODO(E501): shorten line
                    )
                    log.warning(
                        "\nCRF Search exhausted after %d iterations - targets may be unreachable",  # noqa: E501  # TODO(E501): shorten line
                        iteration,
                    )
                    if crf_search_state.get_optimal_crf() is not None:
                        log.info(
                            "Best CRF found: %.1f", crf_search_state.get_optimal_crf()
                        )
                break

            # Display next step
            direction = "down" if next_crf < current_crf else "up"
            crf_delta = next_crf - current_crf
            delta_str = f"{crf_delta:+.1f}"
            if (
                crf_search_state.passing_crf is not None
                and crf_search_state.failing_crf is not None
            ):
                display.console.print(
                    f"[cyan]→ Next: CRF {next_crf:.1f} (refining, {delta_str})[/cyan]"
                )
                log.info("Next CRF: %.1f (refining, delta=%s)", next_crf, delta_str)
            else:
                display.console.print(
                    f"[cyan]→ Next: CRF {next_crf:.1f} (searching {direction}, {delta_str})[/cyan]"  # noqa: E501  # TODO(E501): shorten line
                )
                log.info(
                    "Next CRF: %.1f (searching %s, delta=%s)",
                    next_crf,
                    direction,
                    delta_str,
                )

            current_crf = next_crf

        if iteration >= max_iterations:
            display.console.print()
            display.console.print("[bold red]Maximum CRF iterations reached[/bold red]")
            log.warning("\nMaximum CRF iterations (%d) reached", max_iterations)

    # ========== MULTI-PROFILE SEARCH MODE ==========
    else:
        # Create context factory for multi-profile search
        def ctx_factory(profile: Profile) -> IterationContext:
            return IterationContext(
                input_path=input_path,
                sources=sampled_sources,
                workdir=workdir,
                temp_dir=temp_dir,
                repo_root=repo_root,
                info=info,
                selected_profile=profile,
                total_frames=total_frames,
                guard_start_frames=guard_start_frames,
                guard_end_frames=guard_end_frames,
                vmaf_ref_path=vmaf_ref_path,
                ssim2_ref_path=ssim2_ref_path,
                args=args,
                display=display,
                log=log,
                crop_values=crop_values,
                sharing_samples=sharing_samples,
            )

        # Run multi-profile search
        search_params = MultiProfileSearchParams(
            profiles=multi_profile_list,
            targets=targets,
            crf_start_value=args.crf_start_value,
            crf_interval=args.crf_interval,
            max_iterations=CRF_SEARCH_MAX_ITERATIONS,
            args=args,
            display=display,
            log=log,
        )
        profile_results = run_multi_profile_search(search_params, ctx_factory)

        # Rank results
        ranked_results = rank_profile_results(profile_results, targets)

        if not ranked_results:
            display.console.print(
                "[bold red]No profiles produced valid results[/bold red]"
            )
            log.error("Multi-profile search failed - no valid results")
            return JobResult.failure(input_path, "no profile produced valid results")

        winner = ranked_results[0]

        # Display ranked comparison table
        display_multi_profile_results(
            display.console, ranked_results, targets, args.metric_decimals
        )

        # Display winner
        display.console.print()
        if winner.optimal_crf is not None:
            display.console.print(
                f"[bold green]Winner: {winner.profile_name} at CRF {winner.optimal_crf:.1f}[/bold green]"  # noqa: E501  # TODO(E501): shorten line
            )
        else:
            display.console.print(
                f"[bold green]Winner: {winner.profile_name} (Bitrate mode)[/bold green]"
            )
        bitrate_display = format_bitrate_percentage(
            winner.predicted_bitrate_kbps, info.video_bitrate_kbps
        )
        display.console.print(f"[cyan]Predicted Bitrate: {bitrate_display}[/cyan]")

        # Show warning if applicable
        warned = check_and_display_bitrate_warning(
            display.console,
            log,
            winner.predicted_bitrate_kbps,
            info.video_bitrate_kbps,
            args.predicted_bitrate_warning_percent,
            profile_name=winner.profile_name,
        )

        # The winner busted the budget, so offer the closest encode that fits
        # it: the reader decides whether its metrics are close enough. Points
        # are pooled from profile_results, not ranked_results, so a profile
        # that never converged still contributes what it measured.
        warn_percent = args.predicted_bitrate_warning_percent
        if warned and info.video_bitrate_kbps and warn_percent:
            cap_kbps = info.video_bitrate_kbps * warn_percent / 100.0
            pooled = [p for r in profile_results for p in r.tested_points]
            display_best_within_budget(
                display.console,
                log,
                select_within_budget(pooled, cap_kbps, targets),
                info.video_bitrate_kbps,
                warn_percent,
                targets,
                args.metric_decimals,
                name_profile=True,
            )

        if winner.optimal_crf is not None:
            log.info(
                "Multi-profile search complete - Winner: %s at CRF %.1f (%.0f kbps)",
                winner.profile_name,
                winner.optimal_crf,
                winner.predicted_bitrate_kbps,
            )
        else:
            log.info(
                "Multi-profile search complete - Winner: %s (Bitrate mode, %.0f kbps)",
                winner.profile_name,
                winner.predicted_bitrate_kbps,
            )

        # Set winner's scores for final results display
        optimal_search_scores = {
            k: v for k, v in winner.scores.items() if v is not None
        }

        # Carry the winner through to this job's result row
        result_profile_name = winner.profile_name
        result_crf = winner.optimal_crf
        result_bitrate = winner.predicted_bitrate_kbps

    # ========== DISPLAY FINAL RESULTS ==========
    # Build final scores dict for display
    # Prefer optimal scores from CRF search (if converged) over last iteration's results
    final_scores: dict[str, float | None] = {}
    if optimal_search_scores:
        # Use scores from the optimal CRF (the one that actually met targets)
        final_scores = {k: v for k, v in optimal_search_scores.items()}
    elif vmaf_results:
        # Fallback to last iteration's results (exploration mode or no convergence)
        result = vmaf_results[0]
        final_scores["vmaf_mean"] = result.mean
        final_scores["vmaf_hmean"] = result.harmonic_mean
        final_scores["vmaf_1pct"] = result.p1_low
        final_scores["vmaf_min"] = result.minimum

    # Add SSIM2 scores if available (not tracked in CRF search scores yet)
    if ssim2_results and "ssim2_mean" not in final_scores:
        result = ssim2_results[0]
        final_scores["ssim2_mean"] = result.mean
        final_scores["ssim2_median"] = result.median
        final_scores["ssim2_95pct"] = result.p95_high
        final_scores["ssim2_5pct"] = result.p5_low

    # Display Assessment Summary using shared helper
    # Skip in multi-profile mode since the comparison table already shows everything
    if final_scores and not multi_profile_list:
        # selected_profile is guaranteed when not in multi-profile mode
        assert selected_profile is not None
        # Update targets with final scores (for targeting mode)
        if targets_for_display:
            for target in targets_for_display:
                if target.metric_name in final_scores:
                    target.current_value = final_scores[target.metric_name]

        # targets is None in exploration mode, populated in targeting mode
        display_assessment_summary(
            display.console,
            final_scores,
            targets=targets_for_display,
            iteration=None,
            metric_decimals=args.metric_decimals,
        )

        # Display optimal CRF for CRF search mode
        if not args.assessment_only and optimal_crf is not None:
            display.console.print(
                f"[bold cyan]Optimal CRF: {optimal_crf:.1f}[/bold cyan]"
            )

        # Display predicted bitrate
        # Use the appropriate bitrate value based on mode
        predicted_bitrate_for_display: float
        if args.assessment_only:
            predicted_bitrate_for_display = _predicted_bitrate
        else:
            # CRF search mode
            predicted_bitrate_for_display = optimal_predicted_bitrate

        if predicted_bitrate_for_display > 0:
            bitrate_display = format_bitrate_percentage(
                predicted_bitrate_for_display, info.video_bitrate_kbps
            )
            display.console.print(f"[cyan]Predicted Bitrate: {bitrate_display}[/cyan]")

        # Show warning if applicable
        warned = check_and_display_bitrate_warning(
            display.console,
            log,
            predicted_bitrate_for_display,
            info.video_bitrate_kbps,
            args.predicted_bitrate_warning_percent,
            profile_name=selected_profile.name,
        )

        # Same offer as multi-profile mode, drawn from this profile's own
        # iterations. Assessment-only ran a single encode, so it has no
        # cheaper measurement to fall back to.
        warn_percent = args.predicted_bitrate_warning_percent
        if (
            warned
            and not args.assessment_only
            and info.video_bitrate_kbps
            and warn_percent
        ):
            cap_kbps = info.video_bitrate_kbps * warn_percent / 100.0
            display_best_within_budget(
                display.console,
                log,
                select_within_budget(single_tested_points, cap_kbps, targets),
                info.video_bitrate_kbps,
                warn_percent,
                targets,
                args.metric_decimals,
                name_profile=False,
            )

    log_section(log, "Results")

    # Scores are not restated here: the console already printed them and the
    # transcript carries that into this log verbatim.

    # Single-profile modes fill in the summary row here; the multi-profile branch
    # already set it from the winning profile.
    if not multi_profile_list:
        result_crf = optimal_crf
        result_bitrate = (
            _predicted_bitrate if args.assessment_only else optimal_predicted_bitrate
        )

    converged = (
        args.assessment_only or bool(multi_profile_list) or optimal_crf is not None
    )
    return JobResult(
        input_path=input_path,
        ok=True,
        status="ok" if converged else "no convergence",
        profile_name=result_profile_name,
        optimal_crf=result_crf,
        predicted_bitrate_kbps=result_bitrate,
        source_bitrate_kbps=info.video_bitrate_kbps,
        scores=final_scores,
    )


def main(argv: Iterable[str] | None = None) -> int:
    """CLI wrapper for entry points."""
    args = parse_cli(argv)

    if args.as_one_source and not Path(args.input).is_dir():
        # Erroring costs a re-run; continuing costs a full encode measuring
        # something other than what the flag promised.
        note = (
            "--as-one-source reads a folder of files as one source, "
            f"but {args.input} is a single file."
        )
        PipelineDisplay(show_title=False).console.print(
            f"[bold red]Error:[/bold red] {note}"
        )
        return 1

    if args.as_one_source and args.carry_crf:
        # One job means nothing to carry from.
        note = "--carry-crf has no effect with --as-one-source: a single job."
        PipelineDisplay(show_title=False).console.print(f"[yellow]{note}[/yellow]")

    # A folder means a batch: the same pipeline, once per video inside it.
    # Unless the files are being read as one source, which is a single job.
    if Path(args.input).is_dir() and not args.as_one_source:
        from .batch import run_batch

        # The batch prints the banner once; jobs get a [N/M] header instead,
        # and each job is told the exact folder the batch named for it.
        return run_batch(
            args,
            lambda a, folder: run_pipeline(a, show_title=False, job_folder=folder),
        )

    return 0 if run_pipeline(args).ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
