from __future__ import annotations

import ast
import io
import logging
from pathlib import Path
from typing import override

from rich.console import Console

from videotuner.progress import PipelineDisplay, UserWarningHandler
from videotuner.utils import USER_FACING, warn_user

SRC = Path(__file__).resolve().parents[1] / "src" / "videotuner"

# Every warning that reports an assumption, a fallback, or a reduction in what
# gets measured. A site added to this list without being converted fails here.
# Each row is a module, a fragment of the message, and the number of
# warn_user calls in that module whose message contains it. The count is what
# makes this a guard: membership alone cannot tell "both converted" from "one
# reverted" when two sites share their text, which profiles.py's pair does.
# A count is also what catches a fragment that stops matching because the
# message was reworded.
#
# Counts above 1 are each a real pair, not an accident:
#   profiles.py            the HDR/SDR dict check and its x264 twin
#   tonemapping.py         "ffmpeg not found" also ends "...no Vulkan support"
#   vmaf_assessment.py     the same, for "...no VMAF v1 support"
USER_FACING_WARNINGS = {
    ("create_encodes.py", "No sample frames to analyse", 1),
    ("create_encodes.py", "cropdetect produced no output", 1),
    ("encoding_utils.py", "the index will be built automatically", 1),
    ("media.py", "Unknown chroma_location", 1),
    ("pipeline.py", "VMAF v1 models not found", 1),
    ("pipeline_iteration.py", "No bitrate data available from shared", 1),
    ("pipeline_iteration.py", "No bitrate data available from concatenated", 1),
    ("pipeline_validation.py", "Usable frames", 1),
    ("pipeline_validation.py", "No %s samples possible", 1),
    ("profiles.py", "Skipping parameter", 2),
    ("tonemapping.py", "assuming no Vulkan support", 2),
    ("tonemapping.py", "ffmpeg not found", 1),
    ("utils.py", "Could not parse luminance string", 1),
    ("utils.py", "Could not read mastering display primaries", 1),
    ("vmaf_assessment.py", "assuming no VMAF v1 support", 2),
    ("vmaf_assessment.py", "ffmpeg not found", 1),
    ("x264_params.py", "does not support %d-bit encoding", 1),
}

# vmaf_assessment.py's "Low confidence resolution classification" is
# deliberately not here. It fires on ordinary widescreen shapes (measured:
# 1920x800 and 3840x1600 both score 0.315 confidence, 1920x804 scores 0.319,
# against a 0.5 threshold) and the classification is correct in each case -
# only the UHD test is read from it. select_vmaf_model runs once per CRF
# iteration, so making it user-facing would print several identical lines on
# a healthy run. Leave it as a plain log.warning.


def _warn_user_messages(path: Path) -> list[str]:
    """The message of every warn_user call in one module.

    Each call contributes exactly one string: its literal fragments joined.
    A message built from implicitly concatenated literals, or carrying an
    f-string, therefore still counts once rather than once per fragment.

    Args:
        path: Module to read.

    Returns:
        One joined message per warn_user call, in source order.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    messages: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "warn_user"):
            continue
        messages.append(
            "".join(
                piece.value
                for arg in node.args
                for piece in ast.walk(arg)
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
            )
        )
    return messages


def test_every_user_facing_warning_calls_warn_user() -> None:
    wrong: list[str] = []
    for filename, fragment, expected in sorted(USER_FACING_WARNINGS):
        messages = _warn_user_messages(SRC / filename)
        found = sum(1 for message in messages if fragment in message)
        if found != expected:
            wrong.append(
                f"{filename}: {fragment!r} in {found} calls, expected {expected}"
            )

    assert wrong == []


def _handler_and_sink() -> tuple[UserWarningHandler, io.StringIO]:
    sink = io.StringIO()
    console = Console(file=sink, force_terminal=False, width=200, no_color=True)
    return UserWarningHandler(console), sink


def test_a_marked_warning_reaches_the_console() -> None:
    handler, sink = _handler_and_sink()
    log = logging.getLogger("videotuner.test.marked")
    log.addHandler(handler)
    try:
        warn_user(log, "assuming a %d cd/m2 peak", 1000)
    finally:
        log.removeHandler(handler)

    assert "assuming a 1000 cd/m2 peak" in sink.getvalue()


def test_an_ordinary_warning_does_not() -> None:
    # Housekeeping and search outcomes stay in the job log. A plain
    # log.warning is how a caller says so.
    handler, sink = _handler_and_sink()
    log = logging.getLogger("videotuner.test.plain")
    log.addHandler(handler)
    try:
        log.warning("Failed to clean up pass 1 output: %s", "denied")
    finally:
        log.removeHandler(handler)

    assert sink.getvalue() == ""


def test_the_marked_record_is_still_a_warning_in_the_log() -> None:
    # The terminal is additional, not instead of: the job log must keep it.
    log = logging.getLogger("videotuner.test.record")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        @override
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = _Capture()
    log.addHandler(capture)
    try:
        warn_user(log, "disabling %s", "SSIM2")
    finally:
        log.removeHandler(capture)

    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].getMessage() == "disabling SSIM2"
    assert getattr(records[0], USER_FACING, False) is True


def test_the_handler_ignores_records_below_warning() -> None:
    handler, sink = _handler_and_sink()
    log = logging.getLogger("videotuner.test.level")
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    try:
        log.info("routine progress", extra={USER_FACING: True})
    finally:
        log.removeHandler(handler)

    assert sink.getvalue() == ""


def test_a_bracketed_fragment_reaches_the_console_intact() -> None:
    # A message is arbitrary text, not markup: a filename fragment that looks
    # like a style tag must survive, not be parsed and eaten.
    handler, sink = _handler_and_sink()
    log = logging.getLogger("videotuner.test.bracketed")
    log.addHandler(handler)
    try:
        warn_user(log, "Unknown chroma_location 'xx' from sample [batch2].mkv")
    finally:
        log.removeHandler(handler)

    assert "sample [batch2].mkv" in sink.getvalue()


def test_invalid_markup_does_not_raise_and_still_reaches_the_console() -> None:
    # A message can interpolate an exception string containing something
    # rich's markup parser would reject as a malformed closing tag. That must
    # not raise out of emit and must not silently drop the warning either.
    handler, sink = _handler_and_sink()
    log = logging.getLogger("videotuner.test.invalid_markup")
    log.addHandler(handler)
    try:
        warn_user(log, "failed: %s", "[/opt/x]")
    finally:
        log.removeHandler(handler)

    assert "failed: [/opt/x]" in sink.getvalue()


def test_a_warning_printed_inside_a_live_progress_bar_is_not_corrupted() -> None:
    """Reproduces the case a full run can miss: ``warn_user`` firing while a
    ``Stage``'s Rich ``Live`` context is open, as at the indexer-subprocess-
    failed site inside pipeline.py's "Building {indexer} Index" stage.

    ``force_terminal=True`` makes Rich treat the sink as a real terminal, so
    its ``Progress``/``Live`` machinery does the same in-place, escape-code
    driven redraw it would on a real screen rather than staying silent (Rich
    gates that rendering on ``Console.is_terminal``). Entering a stage
    triggers an immediate first redraw (``Progress.add_task()``'s own
    unconditional refresh, which runs after ``Live.start()``), and exiting
    one triggers an unconditional final redraw before the transient bar
    clears (``Live.stop()``) - both deterministic, so the warning printed in
    between is provably bracketed by live bar frames rather than printed
    before the bar started or after it had already stopped.
    """
    sink = io.StringIO()
    console = Console(file=sink, force_terminal=True, width=200, no_color=True)
    display = PipelineDisplay(console=console, show_title=False)

    handler = UserWarningHandler(display.console)
    log = logging.getLogger("videotuner.test.live_bar")
    log.addHandler(handler)
    try:
        with display.stage("Building FFMS2 Index", total=100) as stage:
            stage.update(completed=10)
            warn_user(log, "indexer subprocess failed: %s", "boom")
            stage.update(completed=100)
    finally:
        log.removeHandler(handler)

    output = sink.getvalue()
    assert "indexer subprocess failed: boom" in output

    # Rich separates a live redraw burst from a plain print with a real
    # newline; the redraw burst itself is many "\r"-joined frames with no
    # real newline between them. Splitting on "\r" as well as "\n" gives one
    # entry per rendered frame, so a warning glued onto a bar frame - rather
    # than printed as its own frame - would show up sharing an entry with
    # bar content instead of getting one to itself.
    frames = [frame for line in output.split("\n") for frame in line.split("\r")]
    bar_frames = [
        i
        for i, frame in enumerate(frames)
        if "Building FFMS2 Index" in frame and "%" in frame
    ]
    warning_frames = [
        i
        for i, frame in enumerate(frames)
        if "indexer subprocess failed: boom" in frame
    ]

    # The bar actually rendered - proves the Live context was genuinely open
    # and driving output, not skipped because of how the sink was built.
    assert bar_frames
    # The warning got a frame of its own ...
    assert warning_frames
    for i in warning_frames:
        assert "Building FFMS2 Index" not in frames[i]
        assert "%" not in frames[i]
    # ... positioned between an earlier and a later bar frame, proving it
    # printed while the bar was live rather than just somewhere in the
    # output.
    assert min(bar_frames) < min(warning_frames) < max(bar_frames)
