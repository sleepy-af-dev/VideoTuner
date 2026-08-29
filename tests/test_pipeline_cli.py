"""Tests for command line parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from videotuner.pipeline import main
from videotuner.pipeline_cli import parse_cli


def _parse(*extra: str):
    return parse_cli(["input.mkv", *extra])


class TestAsOneSourceFlag:
    """Reading every file in a batch folder as one source."""

    def test_off_by_default(self) -> None:
        assert _parse().as_one_source is False

    def test_enabled_by_the_flag(self) -> None:
        assert _parse("--as-one-source").as_one_source is True


class TestAsOneSourceRequiresAFolder:
    """The flag joins several files, so a single file is a mistake worth stopping.

    A flag that quietly does nothing is what the dead output positional was.
    """

    @staticmethod
    def _argv(target: Path) -> list[str]:
        return [
            str(target),
            "--as-one-source",
            "--encoder",
            "x265",
            "--preset",
            "slow",
            "--vmaf-target",
            "95",
        ]

    def test_a_single_file_input_is_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        video = tmp_path / "input.mkv"
        _ = video.write_bytes(b"")

        exit_code = main(self._argv(video))

        assert exit_code == 1
        assert "--as-one-source" in capsys.readouterr().out

    def test_a_folder_input_gets_past_the_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty folder still fails, but for its own reason, not this one."""
        folder = tmp_path / "src"
        folder.mkdir()

        _ = main(self._argv(folder))

        assert "--as-one-source" not in capsys.readouterr().out
