"""Display utilities for VideoTuner pipeline.

This module provides Rich console display functions for pipeline settings,
assessment summaries, and multi-profile comparison results.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from rich.console import Console
from rich.table import Table

from .constants import METRIC_DECIMALS
from .crf_search import QualityTarget
from .pipeline_cli import DEFAULT_CRF_INTERVAL, DEFAULT_CRF_START_VALUE, get_default

if TYPE_CHECKING:
    from .pipeline_types import BudgetPoint, JobResult, MultiProfileResult

#: Metric key to display name, in the order metrics are shown. Single source of
#: truth for both the per-job assessment summary and the batch summary.
METRIC_DISPLAY: list[tuple[str, str]] = [
    # VMAF metrics
    ("vmaf_mean", "VMAF Mean"),
    ("vmaf_hmean", "VMAF Harmonic Mean"),
    ("vmaf_1pct", "VMAF 1% Low"),
    ("vmaf_min", "VMAF Minimum"),
    # SSIMULACRA2 metrics
    ("ssim2_mean", "SSIMULACRA2 Mean"),
    ("ssim2_median", "SSIMULACRA2 Median"),
    ("ssim2_95pct", "SSIMULACRA2 95% High"),
    ("ssim2_5pct", "SSIMULACRA2 5% Low"),
]

METRIC_LABELS: dict[str, str] = dict(METRIC_DISPLAY)


class PipelineArgsProtocol(Protocol):
    """Protocol for pipeline arguments used in display functions."""

    assessment_only: bool
    multi_profile_search: list[str] | None
    profile: str | None
    preset: str | None
    crop_detect: bool
    vmaf: bool
    ssim2: bool
    vmaf_interval_frames: int
    vmaf_region_frames: int
    ssim2_interval_frames: int
    ssim2_region_frames: int
    vmaf_model: str | None
    tonemap: str
    guard_start_percent: float
    guard_end_percent: float
    guard_seconds: float
    predicted_bitrate_warning_percent: float | None
    show_best_within_budget: bool
    continue_budget_search: bool
    vmaf_target: float | None
    vmaf_hmean_target: float | None
    vmaf_1pct_target: float | None
    vmaf_min_target: float | None
    ssim2_mean_target: float | None
    ssim2_median_target: float | None
    ssim2_95pct_target: float | None
    ssim2_5pct_target: float | None


def display_ignored_args_warnings(
    console: Console,
    *,
    bitrate_profile_names: list[str],
    crf_start_value: float,
    crf_interval: float,
) -> None:
    """Display warnings for arguments that will be ignored when using bitrate profiles.

    Only CRF-specific arguments (start value, interval) are warned about.
    Quality targets are now evaluated for bitrate profiles and are not ignored.

    Args:
        console: Rich console for output
        bitrate_profile_names: Names of profiles that use bitrate mode (empty if none)
        crf_start_value: The CRF start value from args
        crf_interval: The CRF interval from args
    """
    if not bitrate_profile_names:
        return

    warnings: list[str] = []
    profiles_str = ", ".join(bitrate_profile_names)

    # Check for explicitly-provided CRF arguments (differ from defaults)
    if crf_start_value != DEFAULT_CRF_START_VALUE:
        warnings.append(
            f"--crf-start-value {crf_start_value} will be ignored for bitrate profiles: {profiles_str}"  # noqa: E501  # TODO(E501): shorten line
        )
    if crf_interval != DEFAULT_CRF_INTERVAL:
        warnings.append(
            f"--crf-interval {crf_interval} will be ignored for bitrate profiles: {profiles_str}"  # noqa: E501  # TODO(E501): shorten line
        )

    # Display warnings. The console tees these into the log.
    for warning in warnings:
        console.print(
            f"[bold yellow]⚠ Warning:[/bold yellow] [yellow]{warning}[/yellow]"
        )

    if warnings:
        console.print()


def display_settings_summary(
    console: Console,
    args: PipelineArgsProtocol,
    multi_profile_display: str = "",
    source_name: str = "",
) -> None:
    """Display active settings summary to console.

    Args:
        console: Rich console for output
        args: Pipeline arguments
        multi_profile_display: Pre-formatted string showing profiles/groups for multi-profile mode
        source_name: Name of the source video file
    """  # noqa: E501  # TODO(E501): shorten line
    console.print()
    console.print("[bold]Settings[/bold]")

    if args.assessment_only:
        console.print("  Mode: Assessment Only")
        console.print(f"  Profile/Preset: {args.profile or f'preset-{args.preset}'}")
    elif args.multi_profile_search:
        console.print("  Mode: Multi-Profile Search")
        console.print(f"  Profiles: {multi_profile_display}")
    else:
        console.print("  Mode: CRF Search")
        console.print(f"  Profile/Preset: {args.profile or f'preset-{args.preset}'}")

    # Build assessments string
    assessments: list[str] = []
    if args.vmaf:
        assessments.append("VMAF")
    if args.ssim2:
        assessments.append("SSIMULACRA2")
    console.print(f"  Assessments: {', '.join(assessments)}")

    console.print(f"  CropDetect: {'Enabled' if args.crop_detect else 'Disabled'}")

    # Log non-default sampling parameters
    if args.vmaf_interval_frames != get_default("vmaf_interval_frames"):
        console.print(f"  VMAF Interval: {args.vmaf_interval_frames} frames")
    if args.vmaf_region_frames != get_default("vmaf_region_frames"):
        console.print(f"  VMAF Region: {args.vmaf_region_frames} frames")
    if args.ssim2_interval_frames != get_default("ssim2_interval_frames"):
        console.print(f"  SSIM2 Interval: {args.ssim2_interval_frames} frames")
    if args.ssim2_region_frames != get_default("ssim2_region_frames"):
        console.print(f"  SSIM2 Region: {args.ssim2_region_frames} frames")

    # Log non-default analysis options
    if args.vmaf_model is not None:
        console.print(f"  VMAF Model: {args.vmaf_model}")
    if args.tonemap != get_default("tonemap"):
        console.print(f"  Tonemap: {args.tonemap}")

    # Log non-default guard bands
    if args.guard_start_percent > 0:
        console.print(f"  Guard Start: {args.guard_start_percent * 100:.1f}%")
    if args.guard_end_percent > 0:
        console.print(f"  Guard End: {args.guard_end_percent * 100:.1f}%")
    if args.guard_seconds > 0:
        console.print(f"  Guard Seconds: {args.guard_seconds:.1f}s")

    # Log bitrate warning if specified
    if args.predicted_bitrate_warning_percent is not None:
        console.print(
            f"  Bitrate Warning: {args.predicted_bitrate_warning_percent:.0f}%"
        )
        if args.show_best_within_budget:
            # Names both, so an implied --show-best-within-budget is visible
            within_budget = (
                "show best, continuing the search"
                if args.continue_budget_search
                else "show best"
            )
            console.print(f"  Within Budget: {within_budget}")

    # Log quality targets if specified
    target_parts: list[str] = []
    if args.vmaf_target is not None:
        target_parts.append(f"VMAF Mean ≥ {args.vmaf_target}")
    if args.vmaf_hmean_target is not None:
        target_parts.append(f"VMAF HMean ≥ {args.vmaf_hmean_target}")
    if args.vmaf_1pct_target is not None:
        target_parts.append(f"VMAF 1% ≥ {args.vmaf_1pct_target}")
    if args.vmaf_min_target is not None:
        target_parts.append(f"VMAF Min ≥ {args.vmaf_min_target}")
    if args.ssim2_mean_target is not None:
        target_parts.append(f"SSIM2 Mean ≥ {args.ssim2_mean_target}")
    if args.ssim2_median_target is not None:
        target_parts.append(f"SSIM2 Median ≥ {args.ssim2_median_target}")
    if args.ssim2_95pct_target is not None:
        target_parts.append(f"SSIM2 95% ≥ {args.ssim2_95pct_target}")
    if args.ssim2_5pct_target is not None:
        target_parts.append(f"SSIM2 5% ≥ {args.ssim2_5pct_target}")

    if target_parts:
        console.print()
        console.print("[bold]Targets[/bold]")
        for target in target_parts:
            console.print(f"  {target}")

    if source_name:
        console.print()
        console.print(f"[bold]Source:[/bold] {source_name}")
        console.print()


def display_assessment_summary(
    console: Console,
    scores: dict[str, float | None],
    targets: list[QualityTarget] | None = None,
    iteration: int | None = None,
    targets_only: bool = False,
    custom_title: str | None = None,
    metric_decimals: int = METRIC_DECIMALS,
) -> None:
    """Display assessment summary with optional target annotations.

    Args:
        console: Rich console for output
        scores: Dictionary of metric scores
        targets: Optional list of quality targets (None for exploration mode)
        iteration: Optional iteration number (None for final display)
        targets_only: If True, only show metrics that are targets (for iteration displays)
        custom_title: Optional custom title (overrides iteration-based title)
        metric_decimals: Decimal places for metric display (default: METRIC_DECIMALS)
    """  # noqa: E501  # TODO(E501): shorten line
    # Build target lookup
    target_map = {}
    if targets:
        target_map: dict[str, QualityTarget] = {t.metric_name: t for t in targets}

    metric_display = METRIC_DISPLAY

    # Determine title
    console.print()
    if custom_title is not None:
        title = custom_title
    elif iteration is not None:
        if targets_only:
            title = f"Targets: Iteration {iteration}"
        else:
            title = f"Assessment Summary: Iteration {iteration}"
    else:
        title = "Assessment Summary"

    # Create single table
    table = Table(
        title=f"[bold cyan]{title}[/bold cyan]",
        show_header=True,
        header_style="bold cyan",
        title_justify="left",
    )

    # Determine if we have targets to show
    has_targets = targets is not None and len(targets) > 0

    table.add_column("Metric", style="white")
    table.add_column("Value", justify="right")
    if has_targets:
        table.add_column("Target Met?", justify="center")
        table.add_column("Delta", justify="right")

    # Add rows for all metrics
    has_rows = False
    for metric_key, display_name in metric_display:
        if metric_key not in scores or scores[metric_key] is None:
            continue

        target = target_map.get(metric_key)

        # Skip non-targets if targets_only mode
        if targets_only and target is None:
            continue

        value = scores[metric_key]
        has_rows = True

        if target is not None and has_targets:
            # This is a target metric - use target's metric_decimals
            decimals = target.metric_decimals
            delta = target.delta()
            delta_str = f"{delta:+.{decimals}f}" if delta is not None else "N/A"

            if target.is_met():
                # Target met: green
                table.add_row(
                    display_name,
                    f"[bold green]{value:.{decimals}f}[/bold green]",
                    "[green]✓[/green]",
                    delta_str,
                )
            else:
                # Target not met: red
                table.add_row(
                    display_name,
                    f"[bold red]{value:.{decimals}f}[/bold red]",
                    "[red]✗[/red]",
                    delta_str,
                )
        else:
            # Not a target: default color (only metric and value when no targets)
            table.add_row(display_name, f"[cyan]{value:.{metric_decimals}f}[/cyan]")

    # Only print table if it has rows
    if has_rows:
        console.print(table)

    console.print()


def format_bitrate_percentage(
    predicted_bitrate_kbps: float,
    input_bitrate_kbps: float | None,
) -> str:
    """Format predicted bitrate with percentage of input if available.

    Args:
        predicted_bitrate_kbps: Predicted output bitrate
        input_bitrate_kbps: Input video bitrate (None if unavailable)

    Returns:
        Formatted string with bitrate and percentage (if available)
    """
    base_str = f"{predicted_bitrate_kbps:,.0f} kbps"

    if (
        input_bitrate_kbps is not None
        and input_bitrate_kbps > 0
        and predicted_bitrate_kbps > 0
    ):
        bitrate_percent = (predicted_bitrate_kbps / input_bitrate_kbps) * 100.0
        return f"{base_str} ({bitrate_percent:.1f}% of input)"

    return base_str


def check_and_display_bitrate_warning(
    console: Console,
    log: logging.Logger,
    predicted_bitrate_kbps: float,
    input_bitrate_kbps: float | None,
    threshold_percent: float | None,
    profile_name: str | None = None,
) -> bool:
    """Check if predicted bitrate exceeds threshold and display warning.

    Args:
        console: Rich console for output
        log: Logger, for the debug lines explaining a skipped warning
        predicted_bitrate_kbps: Predicted output bitrate
        input_bitrate_kbps: Input video bitrate (None if unavailable)
        threshold_percent: Warning threshold as percentage (1-100, None if disabled)
        profile_name: Optional profile name for warning message

    Returns:
        True if the warning was shown, so a caller can follow it with an
        alternative that fits the threshold.
    """
    if threshold_percent is None:
        return False  # Feature disabled

    if input_bitrate_kbps is None or input_bitrate_kbps <= 0:
        log.debug("Predicted bitrate warning skipped: input bitrate unavailable")
        return False

    if predicted_bitrate_kbps <= 0:
        log.debug("Predicted bitrate warning skipped: predicted bitrate unavailable")
        return False

    # Calculate percentage
    bitrate_percent = (predicted_bitrate_kbps / input_bitrate_kbps) * 100.0

    if bitrate_percent > threshold_percent:
        profile_str = f" ({profile_name})" if profile_name else ""
        console.print()
        console.print(
            f"[bold yellow]⚠ Warning: Predicted bitrate{profile_str} "
            + f"({predicted_bitrate_kbps:,.0f} kbps) exceeds {threshold_percent:.0f}% "
            + f"of input bitrate ({input_bitrate_kbps:,.0f} kbps)[/bold yellow]"
        )
        console.print(f"[yellow]  Predicted: {bitrate_percent:.1f}% of input[/yellow]")
        return True

    return False


def display_best_within_budget(
    console: Console,
    log: logging.Logger,
    chosen: BudgetPoint | None,
    input_bitrate_kbps: float,
    threshold_percent: float,
    targets: list[QualityTarget],
    metric_decimals: int,
    *,
    name_profile: bool,
) -> None:
    """Show the best encode that fits the budget, if one was measured.

    Displayed under the warning raised by
    :func:`check_and_display_bitrate_warning`, so someone whose budget is out
    of reach can judge whether the closest thing that does fit is good enough.

    Args:
        console: Rich console for output
        log: Logger, recording the alternative alongside the warning
        chosen: Best measured encode within the budget, or None if nothing fits
        input_bitrate_kbps: Input video bitrate
        threshold_percent: Warning threshold as percentage
        targets: Quality targets, shown as deltas against the chosen encode
        metric_decimals: Decimal places for metric display
        name_profile: Whether to name the profile, which only distinguishes
            anything when more than one was compared
    """
    cap_kbps = input_bitrate_kbps * threshold_percent / 100.0

    if chosen is None:
        subject = "No profile" if name_profile else "No CRF"
        console.print(
            f"[dim]{subject} came in at or below {threshold_percent:.0f}% of input "
            + f"({cap_kbps:,.0f} kbps)[/dim]"
        )
        log.info(
            "Nothing within %.0f%% of input bitrate (%.0f kbps)",
            threshold_percent,
            cap_kbps,
        )
        return

    if chosen.crf is None:
        headline = f"{chosen.profile_name} (Bitrate mode)"
    elif name_profile:
        headline = f"{chosen.profile_name} at CRF {chosen.crf:.1f}"
    else:
        headline = f"CRF {chosen.crf:.1f}"

    bitrate_display = format_bitrate_percentage(
        chosen.predicted_bitrate_kbps, input_bitrate_kbps
    )
    console.print()
    console.print(f"[bold cyan]Best within budget: {headline}[/bold cyan]")
    console.print(f"[cyan]Predicted Bitrate: {bitrate_display}[/cyan]")
    console.print(
        "[dim]Chosen from the CRF values the search tested, which do not cover"
        + " every rate factor[/dim]"
    )
    log.info("Best within budget: %s (%s)", headline, bitrate_display)

    # Copies, so the shared targets keep the winner's values for anything after this.
    scored_targets = [
        replace(t, current_value=chosen.scores.get(t.metric_name)) for t in targets
    ]
    display_assessment_summary(
        console,
        chosen.scores,
        targets=scored_targets,
        targets_only=bool(targets),
        custom_title=f"Within Budget: {headline}",
        metric_decimals=metric_decimals,
    )


def display_multi_profile_results(
    console: Console,
    results: list[MultiProfileResult],
    targets: list[QualityTarget],
    metric_decimals: int = METRIC_DECIMALS,
) -> None:
    """Display ranked multi-profile search results (transposed: metrics as rows, profiles as columns).

    Args:
        console: Rich console for output
        results: Sorted list of profile results (best first)
        targets: Quality targets that were set
        metric_decimals: Decimal places for metric display (default: METRIC_DECIMALS)
    """  # noqa: E501  # TODO(E501): shorten line
    if not results:
        return

    # Determine which metrics to display based on available data
    metric_keys: list[str] = []
    metric_names: list[str] = []

    # Check which metrics are present in results
    if results and results[0].scores:
        sample_scores = results[0].scores

        # VMAF metrics
        if "vmaf_mean" in sample_scores:
            metric_keys.append("vmaf_mean")
            metric_names.append("VMAF Mean")
        if "vmaf_hmean" in sample_scores:
            metric_keys.append("vmaf_hmean")
            metric_names.append("VMAF Harmonic")
        if "vmaf_1pct" in sample_scores:
            metric_keys.append("vmaf_1pct")
            metric_names.append("VMAF 1% Low")
        if "vmaf_min" in sample_scores:
            metric_keys.append("vmaf_min")
            metric_names.append("VMAF Min")

        # SSIMULACRA2 metrics
        if "ssim2_mean" in sample_scores:
            metric_keys.append("ssim2_mean")
            metric_names.append("SSIMULACRA2 Mean")
        if "ssim2_median" in sample_scores:
            metric_keys.append("ssim2_median")
            metric_names.append("SSIM2 Median")
        if "ssim2_95pct" in sample_scores:
            metric_keys.append("ssim2_95pct")
            metric_names.append("SSIM2 95% High")
        if "ssim2_5pct" in sample_scores:
            metric_keys.append("ssim2_5pct")
            metric_names.append("SSIM2 5% Low")

    # Create table (transposed: metrics as rows, profiles as columns)
    table = Table(
        title="[bold cyan]Multi-Profile Comparison Results[/bold cyan]",
        show_header=True,
        header_style="bold cyan",
        title_justify="left",
    )

    # Check if we have any CRF profiles
    has_crf_profiles = any(result.optimal_crf is not None for result in results)

    # Add columns: Metric name + one column per profile
    table.add_column("Metric", style="white", min_width=18)
    for rank, result in enumerate(results, 1):
        is_winner = rank == 1  # Always highlight the top-ranked profile
        profile_header = f"#{rank} {result.profile_name}"
        if is_winner:
            table.add_column(
                profile_header, justify="right", style="bold green", min_width=12
            )
        else:
            table.add_column(profile_header, justify="right", min_width=12)

    # Add rows: one row per metric
    has_checkmarks = False
    for _metric_idx, (metric_key, metric_name) in enumerate(
        zip(metric_keys, metric_names)
    ):
        row_values = [metric_name]

        # Check if this metric is a target
        is_target = any(t.metric_name == metric_key for t in targets)

        # Get decimals from target if this is a target metric, else use parameter
        target_for_metric = next(
            (t for t in targets if t.metric_name == metric_key), None
        )
        decimals = (
            target_for_metric.metric_decimals if target_for_metric else metric_decimals
        )

        for rank, result in enumerate(results, 1):
            value = result.scores.get(metric_key)

            if value is not None:
                formatted_value = f"{value:.{decimals}f}"
                # Add checkmark if target is met (for profiles where targets were evaluated)  # noqa: E501  # TODO(E501): shorten line
                if is_target and result.meets_all_targets is not None:
                    target = next(
                        (t for t in targets if t.metric_name == metric_key), None
                    )
                    if target and value >= target.target_value:
                        formatted_value = f"✓ {formatted_value}"
                        has_checkmarks = True
                row_values.append(formatted_value)
            else:
                row_values.append("-")

        table.add_row(*row_values)

    # Add separator row (empty)
    if metric_keys:
        table.add_row(*[""] * (len(results) + 1))

    # Add Avg Bitrate row
    row_values: list[str] = ["Predicted Bitrate (kbps)"]
    for result in results:
        row_values.append(f"{result.predicted_bitrate_kbps:,.0f}")
    table.add_row(*row_values)

    # Add Mode row (CRF or Bitrate)
    row_values = ["Mode"]
    for result in results:
        if result.optimal_crf is not None:
            row_values.append("CRF")
        else:
            row_values.append("Bitrate")
    table.add_row(*row_values)

    # Add Optimal CRF row (only if at least one profile is CRF mode)
    if has_crf_profiles:
        row_values = ["Optimal CRF"]
        for result in results:
            if result.optimal_crf is not None:
                row_values.append(f"{result.optimal_crf:.1f}")
            else:
                row_values.append("-")
        table.add_row(*row_values)

    # Add Targets Met row (when targets exist and any profile was evaluated against them)  # noqa: E501  # TODO(E501): shorten line
    has_evaluated_profiles = any(r.meets_all_targets is not None for r in results)
    if targets and has_evaluated_profiles:
        row_values = ["All Targets Met"]
        for result in results:
            if result.meets_all_targets is True:
                row_values.append("✓")
            elif result.meets_all_targets is False:
                row_values.append("✗")
            else:
                # meets_all_targets is None (not evaluated against targets)
                row_values.append("-")
        table.add_row(*row_values)

    console.print()
    console.print(table)

    # Show appropriate ranking explanation based on profile types
    all_abr = not has_crf_profiles
    has_targets_specified = len(targets) > 0

    if all_abr and has_targets_specified:
        console.print(
            "[dim]Ranked by target achievement, then quality score priority (winner highlighted)[/dim]"  # noqa: E501  # TODO(E501): shorten line
        )
    elif all_abr:
        console.print(
            "[dim]Ranked by quality score priority (winner highlighted)[/dim]"
        )
    elif has_targets_specified:
        console.print(
            "[dim]Ranked by target achievement, then lowest predicted bitrate with quality tiebreaker (winner highlighted)[/dim]"  # noqa: E501  # TODO(E501): shorten line
        )
    else:
        console.print(
            "[dim]Ranked by lowest predicted bitrate with quality tiebreaker (winner highlighted)[/dim]"  # noqa: E501  # TODO(E501): shorten line
        )

    # Show key legend only when checkmarks are displayed
    if has_checkmarks:
        console.print("[dim]Key: ✓ = Target met[/dim]")


def display_batch_summary(
    console: Console,
    results: list[JobResult],
    targets: list[QualityTarget] | None = None,
    metric_decimals: int = METRIC_DECIMALS,
) -> None:
    """Display one row per job: what to use for each file in the batch.

    Only metrics that were actually targeted get a column. The full per-metric
    breakdown for any one job is in that job's own log, so repeating all eight
    metrics across every row would cost width without adding information.

    Args:
        console: Rich console for output
        results: One result per job, in the order the jobs ran
        targets: Quality targets that were set (None or empty if exploring)
        metric_decimals: Decimal places for metric display
    """
    if not results:
        return

    targets = targets or []

    table = Table(title="Batch Summary", title_style="bold", show_lines=False)
    table.add_column("File", style="cyan", overflow="fold")
    table.add_column("Profile")
    table.add_column("CRF", justify="right")
    table.add_column("Bitrate", justify="right")
    for target in targets:
        label = METRIC_LABELS.get(target.metric_name, target.metric_name)
        table.add_column(f"{label}\n≥ {target.target_value:g}", justify="right")
    table.add_column("Status")

    for result in results:
        row = [
            result.input_path.name,
            result.profile_name or "-",
            f"{result.optimal_crf:.1f}" if result.optimal_crf is not None else "-",
            format_bitrate_percentage(
                result.predicted_bitrate_kbps, result.source_bitrate_kbps
            )
            if result.predicted_bitrate_kbps > 0
            else "-",
        ]
        for target in targets:
            value = result.scores.get(target.metric_name)
            if value is None:
                row.append("-")
                continue
            met = round(value, metric_decimals) >= round(
                target.target_value, metric_decimals
            )
            mark = "[green]✓[/green]" if met else "[red]✗[/red]"
            row.append(f"{value:.{metric_decimals}f} {mark}")

        row.append(f"[{'green' if result.ok else 'red'}]{result.status}[/]")
        table.add_row(*row)

    console.print()
    console.print(table)

    succeeded = sum(1 for r in results if r.ok)
    total = len(results)
    if succeeded == total:
        console.print(f"[bold green]All {total} job(s) succeeded[/bold green]")
    else:
        console.print(
            f"[bold red]{total - succeeded} of {total} job(s) failed[/bold red]"
        )
