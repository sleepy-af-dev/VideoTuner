"""Tests for batch processing.

These use a stubbed job runner: the point is the batch loop's bookkeeping, not
the encoding it drives.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from videotuner import batch
from videotuner.batch import discover_videos, run_batch
from videotuner.pipeline_cli import PipelineArgs
from videotuner.pipeline_types import JobResult


def _touch(folder: Path, *names: str) -> None:
    for name in names:
        _ = (folder / name).write_bytes(b"")


def _args(input_path: Path, workdir: Path) -> PipelineArgs:
    """Args that validate without needing profiles.yaml on disk."""
    return PipelineArgs(
        input=input_path,
        workdir=workdir,
        encoder="x265",
        preset="slow",
        vmaf_target=95.0,
    )


def _ok(args: PipelineArgs) -> JobResult:
    return JobResult(input_path=args.input, ok=True, profile_name="preset-slow")


class TestDiscoverVideos:
    def test_filters_to_video_extensions(self, tmp_path: Path) -> None:
        _touch(tmp_path, "a.mkv", "b.mp4", "notes.txt", "cover.jpg", "sub.srt")
        assert [p.name for p in discover_videos(tmp_path)] == ["a.mkv", "b.mp4"]

    def test_extension_match_is_case_insensitive(self, tmp_path: Path) -> None:
        _touch(tmp_path, "SHOUTY.MKV")
        assert [p.name for p in discover_videos(tmp_path)] == ["SHOUTY.MKV"]

    def test_top_level_only(self, tmp_path: Path) -> None:
        _touch(tmp_path, "top.mkv")
        nested = tmp_path / "subfolder"
        nested.mkdir()
        _touch(nested, "buried.mkv")
        assert [p.name for p in discover_videos(tmp_path)] == ["top.mkv"]

    def test_sorted_by_name(self, tmp_path: Path) -> None:
        _touch(tmp_path, "c.mkv", "a.mkv", "B.mkv")
        assert [p.name for p in discover_videos(tmp_path)] == [
            "a.mkv",
            "B.mkv",
            "c.mkv",
        ]

    def test_empty_folder(self, tmp_path: Path) -> None:
        assert discover_videos(tmp_path) == []


class TestJobFolderNames:
    def test_collisions_are_suffixed_not_overwritten(self, tmp_path: Path) -> None:
        videos = [tmp_path / "clip.mkv", tmp_path / "clip.mp4", tmp_path / "other.mkv"]
        assert batch.job_folder_names(videos) == ["clip", "clip_2", "other"]

    def test_names_are_fitted_to_the_budget(self, tmp_path: Path) -> None:
        videos = [tmp_path / (("long" * 40) + ".mkv")]
        assert all(len(n) <= 50 for n in batch.job_folder_names(videos, 50))

    def test_truncated_names_do_not_collide(self, tmp_path: Path) -> None:
        shared = "shared-leading-portion-of-two-input-names-"
        videos = [tmp_path / f"{shared}first.mkv", tmp_path / f"{shared}second.mkv"]
        names = batch.job_folder_names(videos, 45)
        assert len(set(names)) == 2, "two sources must not share one job folder"


class TestRunBatch:
    def test_empty_folder_is_an_error(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        args = _args(source, tmp_path / "out")
        assert run_batch(args, _ok) == 1

    def test_runs_every_video_and_succeeds(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv")
        seen: list[Path] = []

        def runner(args: PipelineArgs) -> JobResult:
            seen.append(args.input)
            return _ok(args)

        assert run_batch(_args(source, tmp_path / "out"), runner) == 0
        assert [p.name for p in seen] == ["a.mkv", "b.mkv"]

    def test_one_failure_does_not_stop_the_batch(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv", "c.mkv")
        seen: list[str] = []

        def runner(args: PipelineArgs) -> JobResult:
            seen.append(args.input.name)
            if args.input.name == "b.mkv":
                return JobResult.failure(args.input, "invalid video file")
            return _ok(args)

        exit_code = run_batch(_args(source, tmp_path / "out"), runner)
        assert seen == ["a.mkv", "b.mkv", "c.mkv"]
        assert exit_code == 1

    def test_a_raising_job_does_not_stop_the_batch(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv", "c.mkv")
        seen: list[str] = []

        def runner(args: PipelineArgs) -> JobResult:
            seen.append(args.input.name)
            if args.input.name == "b.mkv":
                raise RuntimeError("encoder died")
            return _ok(args)

        exit_code = run_batch(_args(source, tmp_path / "out"), runner)
        assert seen == ["a.mkv", "b.mkv", "c.mkv"]
        assert exit_code == 1

    def test_each_job_gets_its_own_folder_under_the_batch(self, tmp_path: Path) -> None:
        """The mechanism that makes a batched job folder identical to a single one.

        Each job is handed a workdir of ``<batch>/<stem>`` and no explicit log
        file, so it names its log ``<stem>.log`` inside that folder by exactly
        the same code path a single-file run takes.
        """
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv")
        batch_folder = tmp_path / "out"
        handed: list[PipelineArgs] = []

        def runner(args: PipelineArgs) -> JobResult:
            handed.append(args)
            return _ok(args)

        _ = run_batch(_args(source, batch_folder), runner)
        assert [a.workdir for a in handed] == [
            batch_folder / "a",
            batch_folder / "b",
        ]
        assert all(a.log_file is None for a in handed)

    def test_batch_log_is_written_to_the_batch_folder(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv")
        batch_folder = tmp_path / "out"

        _ = run_batch(_args(source, batch_folder), _ok)
        assert (batch_folder / "batch.log").exists()

    def test_batch_log_handler_is_removed_afterwards(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv")
        before = list(logging.getLogger().handlers)

        _ = run_batch(_args(source, tmp_path / "out"), _ok)
        assert logging.getLogger().handlers == before


class TestJobLogHandlerLifecycle:
    """The leak that would put job 1's log inside job 20's.

    ``run_pipeline`` wraps the body precisely so a handler the body attaches
    cannot survive the job. Verified against the wrapper rather than a real
    encode, since that is where the guarantee lives.
    """

    def test_handler_attached_by_a_job_is_detached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from videotuner import pipeline

        def fake_body(args: PipelineArgs, **_kwargs: object) -> JobResult:
            handler = logging.FileHandler(tmp_path / "job.log", encoding="utf-8")
            logging.getLogger().addHandler(handler)
            return JobResult(input_path=args.input, ok=True)

        monkeypatch.setattr(pipeline, "_run_pipeline_body", fake_body)
        before = list(logging.getLogger().handlers)

        result = pipeline.run_pipeline(_args(tmp_path / "x.mkv", tmp_path))

        assert result.ok
        assert logging.getLogger().handlers == before

    def test_handler_is_detached_even_when_the_job_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from videotuner import pipeline

        def fake_body(args: PipelineArgs, **_kwargs: object) -> JobResult:
            handler = logging.FileHandler(tmp_path / "job.log", encoding="utf-8")
            logging.getLogger().addHandler(handler)
            raise RuntimeError(f"encoder died on {args.input.name}")

        monkeypatch.setattr(pipeline, "_run_pipeline_body", fake_body)
        before = list(logging.getLogger().handlers)

        with pytest.raises(RuntimeError):
            _ = pipeline.run_pipeline(_args(tmp_path / "x.mkv", tmp_path))

        assert logging.getLogger().handlers == before
