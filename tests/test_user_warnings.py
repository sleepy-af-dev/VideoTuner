from __future__ import annotations

import io
import logging

from rich.console import Console

from videotuner.progress import UserWarningHandler
from videotuner.utils import USER_FACING, warn_user


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
