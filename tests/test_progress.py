"""Tests for the terminal transcript written into job and batch logs."""

from __future__ import annotations

import io
import logging
from collections.abc import Generator

import pytest
from rich.table import Table

from videotuner.constants import MAX_USABLE_PATH
from videotuner.progress import (
    TRANSCRIPT_WIDTH,
    PipelineDisplay,
    TranscriptFormatter,
)


@pytest.fixture
def transcript() -> Generator[tuple[PipelineDisplay, io.StringIO]]:
    """A display whose console output is captured the way a job log captures it."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(TranscriptFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        yield PipelineDisplay(show_title=False), buffer
    finally:
        root.removeHandler(handler)


class TestTranscriptWrapping:
    """A wrapped path is unreadable and cannot be grepped out of a log."""

    def test_a_maximum_length_path_is_not_wrapped(
        self, transcript: tuple[PipelineDisplay, io.StringIO]
    ) -> None:
        display, buffer = transcript
        path = "C:\\vt\\" + "x" * (MAX_USABLE_PATH - 6)

        display.console.print(f"Batch folder: {path}")

        lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1, "a path must not be split across lines"
        assert path in lines[0]

    def test_text_longer_than_the_render_width_is_still_not_wrapped(
        self, transcript: tuple[PipelineDisplay, io.StringIO]
    ) -> None:
        """Width gives tables room; soft wrap is what makes text unbreakable.

        A message carrying two paths can outrun any fixed width, so the
        guarantee must not depend on the width being large enough.
        """
        display, buffer = transcript
        line = "x" * (TRANSCRIPT_WIDTH * 2)

        display.console.print(line)

        lines = [ln for ln in buffer.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_a_wide_table_is_not_squeezed(
        self, transcript: tuple[PipelineDisplay, io.StringIO]
    ) -> None:
        """soft_wrap does not apply to tables: they lay out against the width.

        Too narrow a width and a long cell is truncated, losing the filename
        the summary row is about.
        """
        display, buffer = transcript
        name = "a-long-example-input-filename-that-goes-on" * 2
        table = Table()
        for column in ("File", "Profile", "Bitrate", "Status"):
            table.add_column(column)
        table.add_row(name, "Example (x265)", "2,627 kbps (5.0% of input)", "ok")

        display.console.print(table)

        out = buffer.getvalue()
        assert name in out, "the full filename must survive into the log"
        assert "─" in out, "box drawing means the table laid out rather than wrapping"
