"""Batch processing: run the job pipeline over every video in a folder.

A batch is a set of jobs sharing one set of settings, produced from the videos
found in a single input folder. Jobs run sequentially - the encoders already
saturate every core, so running two at once makes both slower and interleaves
their output into mush.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .constants import JOB_FOLDER_MIN_CHARS
from .encoder_type import EncoderType
from .encoding_utils import is_hdr_video
from .media import InvalidVideoFileError, parse_video_info
from .pipeline_cli import PipelineArgs, build_arg_parser, validate_args
from .pipeline_display import display_batch_summary
from .pipeline_types import JobResult
from .pipeline_validation import build_targets
from .progress import PipelineDisplay, TranscriptFormatter
from .utils import (
    configure_logging,
    ensure_dir,
    fit_path_segment,
    get_app_root,
    job_folder_budget,
    log_section,
    sanitize_filename,
)

#: Extensions treated as video inputs. An allowlist rather than "ffprobe
#: everything and skip what fails" - probing every stray file in a folder is
#: slow, and a match here is a much clearer signal of intent.
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mkv", ".mp4", ".m4v", ".mov", ".ts", ".m2ts", ".avi", ".webm"}
)


def discover_videos(folder: Path) -> list[Path]:
    """Return the video files at the top level of ``folder``, sorted by name.

    Top level only. Recursion would make it easy to kick off an unattended
    multi-hour run over a whole library by naming one directory above it.
    """
    return sorted(
        (
            p
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda p: p.name.lower(),
    )


def job_folder_names(videos: list[Path], budget: int | None = None) -> list[str]:
    """Map each input to a unique job folder name, fitted to ``budget``.

    ``clip.mkv`` and ``clip.mp4`` both sanitize to ``clip``, so later
    collisions get a numeric suffix rather than overwriting the earlier job.
    Shortening happens first, so the suffix disambiguates the name that is
    actually used.
    """
    names: list[str] = []
    seen: dict[str, int] = {}
    for video in videos:
        base = sanitize_filename(video.stem)
        if budget is not None:
            base = fit_path_segment(base, budget)
        count = seen.get(base, 0)
        seen[base] = count + 1
        names.append(base if count == 0 else f"{base}_{count + 1}")
    return names


def _prescan(
    videos: list[Path],
    display: PipelineDisplay,
    x264_profile_names: list[str],
    ffprobe_bin: str,
) -> None:
    """Warn up front about files that cannot succeed with these settings.

    Warns rather than aborts: the point of finding out at minute zero is to
    decide whether to let the rest of the batch run, and usually you will.
    """
    problems: list[str] = []
    for video in videos:
        try:
            # log_hdr_metadata=False: the job that runs this file logs its own
            # metadata, and the pre-scan should not fill batch.log with it.
            info = parse_video_info(
                video, ffprobe_bin=ffprobe_bin, log_hdr_metadata=False
            )
        except InvalidVideoFileError as e:
            problems.append(f"{video.name}: {e}")
            continue
        if x264_profile_names and is_hdr_video(info.color_trc):
            profiles = ", ".join(x264_profile_names)
            reason = "HDR source, but x264 cannot carry HDR10 metadata"
            problems.append(f"{video.name}: {reason} (profile(s): {profiles})")

    if not problems:
        return

    headline = f"⚠ {len(problems)} of {len(videos)} file(s) are expected to fail:"
    display.console.print(f"[bold yellow]{headline}[/bold yellow]")
    for problem in problems:
        display.console.print(f"[yellow]  {problem}[/yellow]")
    display.console.print("[yellow]The remaining files will still run.[/yellow]")
    display.console.print()


def run_batch(
    args: PipelineArgs, run_job: Callable[[PipelineArgs, Path], JobResult]
) -> int:
    """Run every video in ``args.input`` as a job. Returns a process exit code.

    ``run_job`` takes the job's arguments and the exact folder it must write to.
    The folder is passed explicitly rather than through ``args.workdir`` because
    that is the parent a run folder is created in, and a job inside a batch has
    already had its folder named and fitted here.

    ``run_job`` is passed in rather than imported so this module does not depend
    on the pipeline it drives - which also lets the tests stub it out.
    """
    input_folder = Path(args.input)
    videos = discover_videos(input_folder)

    # Before anything prints: the console tees into the log, and without a
    # configured root level those records are dropped on the floor.
    _ = configure_logging(verbose=bool(args.verbose), quiet=bool(args.quiet))
    display = PipelineDisplay(show_title=True)
    log = logging.getLogger(__name__)

    if not videos:
        display.console.print(
            f"\n[bold red]Error:[/bold red] No video files found in {input_folder}\n"
        )
        return 1

    # Settings are invariant across the batch, so validate once. A bad flag
    # aborts here (via parser.error) instead of failing identically N times.
    validation = validate_args(args, build_arg_parser())
    candidates = (
        [validation.selected_profile]
        if validation.selected_profile
        else validation.multi_profile_list
    )
    x264_profile_names = [p.name for p in candidates if p.encoder == EncoderType.X264]
    slugs = [p.name for p in candidates]

    repo_root = get_app_root()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --workdir is the parent that run folders go in, never the batch folder
    # itself, so every run gets its own timestamped folder and repeat runs
    # cannot overwrite each other.
    jobs_root = args.workdir if args.workdir else repo_root / "jobs"
    folder_name = sanitize_filename(input_folder.resolve().name) or "batch"
    batch_folder = jobs_root / f"{folder_name}_{timestamp}"
    # The batch folder is shared by every job, so it can only be shortened
    # once, here, before any job folder is named.
    shortfall = JOB_FOLDER_MIN_CHARS - job_folder_budget(batch_folder, slugs)
    if shortfall > 0:
        folder_name = fit_path_segment(
            folder_name, max(1, len(folder_name) - shortfall)
        )
        batch_folder = jobs_root / f"{folder_name}_{timestamp}"
        note = f"Batch folder name shortened to fit the path limit: {folder_name}"
        display.console.print(f"[yellow]{note}[/yellow]")
    _ = ensure_dir(batch_folder)

    # Job folder names are fitted against the batch folder actually chosen.
    budget = job_folder_budget(batch_folder, slugs)
    if budget < JOB_FOLDER_MIN_CHARS:
        warning = (
            f"⚠ {batch_folder} leaves only {budget} characters for job folder "
            f"names. Paths may exceed the Windows limit; use a shorter --workdir."
        )
        display.console.print(f"[bold yellow]{warning}[/bold yellow]")

    # Batch-level output and job output both reach the root logger through the
    # same console, so the batch handler is muted while a job runs. That detail
    # is already in the job's own log.
    in_job = False
    batch_handler = logging.FileHandler(batch_folder / "batch.log", encoding="utf-8")
    batch_handler.setFormatter(TranscriptFormatter())
    batch_handler.addFilter(lambda record: not in_job)
    logging.getLogger().addHandler(batch_handler)

    try:
        log_section(log, "Batch")
        display.console.print(f"[cyan]Batch folder:[/cyan] {batch_folder}")
        display.console.print(
            f"[cyan]Found {len(videos)} video(s) in[/cyan] {input_folder}"
        )
        display.console.print()

        _prescan(videos, display, x264_profile_names, args.ffprobe_bin)

        results: list[JobResult] = []
        # --carry-crf: the CRF the previous job settled on, used as the next
        # job's starting point. Left untouched by a job that fails or does not
        # converge, so the next job starts from the last CRF that was found.
        carried_crf: float | None = None

        for index, (video, folder_name) in enumerate(
            zip(videos, job_folder_names(videos, budget), strict=True), start=1
        ):
            display.console.print(
                f"[bold]\\[{index}/{len(videos)}][/bold] [cyan]{video.name}[/cyan]"
            )
            if folder_name != sanitize_filename(video.stem):
                # The job never sees the untruncated name, so the batch reports
                # this: an unexplained hash suffix in a folder name is worse
                # than a line of output.
                note = f"Job folder name shortened to fit the path limit: {folder_name}"
                display.console.print(f"[yellow]{note}[/yellow]")
            start_crf = carried_crf if carried_crf is not None else args.crf_start_value
            if carried_crf is not None:
                note = f"Starting at CRF {start_crf:.1f} carried from the previous job"
                display.console.print(f"[dim]{note}[/dim]")
            job_args = replace(
                args,
                input=video,
                log_file=None,
                crf_start_value=start_crf,
            )
            in_job = True
            try:
                result = run_job(job_args, batch_folder / folder_name)
                results.append(result)
                if args.carry_crf and result.optimal_crf is not None:
                    carried_crf = result.optimal_crf
            except Exception as e:
                # One job's crash must not take the rest of the batch with it.
                in_job = False
                log.exception("Job failed: %s", video.name)
                display.console.print(
                    f"[bold red]Job failed:[/bold red] {video.name}: {e}"
                )
                results.append(JobResult.failure(video, f"failed: {type(e).__name__}"))
            finally:
                in_job = False

        display_batch_summary(
            display.console,
            results,
            targets=build_targets(args),
            metric_decimals=args.metric_decimals,
        )

        return 0 if all(r.ok for r in results) else 1
    finally:
        logging.getLogger().removeHandler(batch_handler)
        batch_handler.close()
