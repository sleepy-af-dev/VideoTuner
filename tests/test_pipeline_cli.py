"""Tests for command line parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from videotuner.pipeline import main
from videotuner.pipeline_cli import parse_cli


def _argv(target: Path) -> list[str]:
    """A complete command line reading a folder as one source."""
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

    def test_a_single_file_input_is_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        video = tmp_path / "input.mkv"
        _ = video.write_bytes(b"")

        exit_code = main(_argv(video))

        assert exit_code == 1
        assert "--as-one-source" in capsys.readouterr().out

    def test_a_folder_input_gets_past_the_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty folder still fails, but for its own reason, not this one."""
        folder = tmp_path / "src"
        folder.mkdir()

        _ = main(_argv(folder))

        assert "--as-one-source" not in capsys.readouterr().out


class TestAsOneSourceRouting:
    """A folder read as one source is a single job, not a batch."""

    def test_a_folder_with_the_flag_does_not_run_a_batch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A batch would also reach the job, so assert the batch is never entered."""
        from videotuner import batch

        folder = tmp_path / "src"
        folder.mkdir()
        _ = (folder / "a.mkv").write_bytes(b"")

        called: list[str] = []

        def fake_batch(*_args: object) -> int:
            called.append("batch")
            return 0

        monkeypatch.setattr(batch, "run_batch", fake_batch)

        _ = main(_argv(folder))

        assert called == [], "one source is a single job, not a batch of one"

    def test_a_folder_without_the_flag_runs_a_batch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from videotuner import batch

        folder = tmp_path / "src"
        folder.mkdir()
        _ = (folder / "a.mkv").write_bytes(b"")

        called: list[str] = []

        def fake_batch(*_args: object) -> int:
            called.append("batch")
            return 0

        monkeypatch.setattr(batch, "run_batch", fake_batch)

        argv = [a for a in _argv(folder) if a != "--as-one-source"]
        _ = main(argv)

        assert called == ["batch"]
