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


def _run_folder(workdir: Path) -> Path:
    """The single timestamped run folder a batch creates inside --workdir."""
    children = [p for p in workdir.iterdir() if p.is_dir()]
    assert len(children) == 1, f"expected one run folder, found {children}"
    return children[0]


def _ok(args: PipelineArgs, _folder: Path) -> JobResult:
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

        def runner(args: PipelineArgs, folder: Path) -> JobResult:
            seen.append(args.input)
            return _ok(args, folder)

        assert run_batch(_args(source, tmp_path / "out"), runner) == 0
        assert [p.name for p in seen] == ["a.mkv", "b.mkv"]

    def test_one_failure_does_not_stop_the_batch(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv", "c.mkv")
        seen: list[str] = []

        def runner(args: PipelineArgs, folder: Path) -> JobResult:
            seen.append(args.input.name)
            if args.input.name == "b.mkv":
                return JobResult.failure(args.input, "invalid video file")
            return _ok(args, folder)

        exit_code = run_batch(_args(source, tmp_path / "out"), runner)
        assert seen == ["a.mkv", "b.mkv", "c.mkv"]
        assert exit_code == 1

    def test_a_raising_job_does_not_stop_the_batch(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv", "c.mkv")
        seen: list[str] = []

        def runner(args: PipelineArgs, folder: Path) -> JobResult:
            seen.append(args.input.name)
            if args.input.name == "b.mkv":
                raise RuntimeError("encoder died")
            return _ok(args, folder)

        exit_code = run_batch(_args(source, tmp_path / "out"), runner)
        assert seen == ["a.mkv", "b.mkv", "c.mkv"]
        assert exit_code == 1

    def test_each_job_is_told_the_exact_folder_to_write_to(
        self, tmp_path: Path
    ) -> None:
        """A job's folder is named by the batch and handed over explicitly.

        It must not arrive as ``args.workdir``: that is the parent a run folder
        is created in, so the job would nest a second timestamped folder inside
        the one the batch already made for it.
        """
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv", "b.mkv")
        workdir = tmp_path / "out"
        handed: list[tuple[PipelineArgs, Path]] = []

        def runner(args: PipelineArgs, folder: Path) -> JobResult:
            handed.append((args, folder))
            return _ok(args, folder)

        _ = run_batch(_args(source, workdir), runner)
        batch_folder = _run_folder(workdir)
        assert [folder for _, folder in handed] == [
            batch_folder / "a",
            batch_folder / "b",
        ]
        assert all(a.log_file is None for a, _ in handed)

    def test_batch_log_is_written_to_the_batch_folder(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv")
        workdir = tmp_path / "out"

        _ = run_batch(_args(source, workdir), _ok)
        assert (_run_folder(workdir) / "batch.log").exists()

    def test_workdir_is_a_parent_not_the_run_folder(self, tmp_path: Path) -> None:
        """--workdir holds run folders, so repeat runs cannot overwrite each other."""
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv")
        workdir = tmp_path / "out"

        _ = run_batch(_args(source, workdir), _ok)

        run_folder = _run_folder(workdir)
        assert run_folder.parent == workdir
        assert run_folder.name.startswith("src_"), "run folder carries name and stamp"

    def test_batch_log_handler_is_removed_afterwards(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, "a.mkv")
        before = list(logging.getLogger().handlers)

        _ = run_batch(_args(source, tmp_path / "out"), _ok)
        assert logging.getLogger().handlers == before


class TestCarryCrf:
    """--carry-crf starts each job where the previous one settled."""

    @staticmethod
    def _collect(
        tmp_path: Path, carry: bool, optimal: list[float | None]
    ) -> list[float]:
        """Run a batch and return the CRF each job was told to start at."""
        source = tmp_path / "src"
        source.mkdir()
        _touch(source, *[f"{chr(ord('a') + i)}.mkv" for i in range(len(optimal))])
        starts: list[float] = []
        remaining = list(optimal)

        def runner(args: PipelineArgs, _folder: Path) -> JobResult:
            starts.append(args.crf_start_value)
            crf = remaining.pop(0)
            if crf is None:
                return JobResult.failure(args.input, "no convergence")
            return JobResult(input_path=args.input, ok=True, optimal_crf=crf)

        job_args = _args(source, tmp_path / "out")
        job_args.crf_start_value = 28.0
        job_args.carry_crf = carry
        _ = run_batch(job_args, runner)
        return starts

    def test_disabled_by_default_every_job_uses_the_start_value(
        self, tmp_path: Path
    ) -> None:
        starts = self._collect(tmp_path, carry=False, optimal=[20.0, 22.0, 24.0])
        assert starts == [28.0, 28.0, 28.0]

    def test_each_job_starts_where_the_previous_one_landed(
        self, tmp_path: Path
    ) -> None:
        starts = self._collect(tmp_path, carry=True, optimal=[20.0, 22.0, 24.0])
        assert starts == [28.0, 20.0, 22.0]

    def test_a_job_without_a_result_does_not_reset_the_carried_value(
        self, tmp_path: Path
    ) -> None:
        """A failed job shouldn't throw away the best guess we already had."""
        starts = self._collect(tmp_path, carry=True, optimal=[20.0, None, 24.0])
        assert starts == [28.0, 20.0, 20.0]


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
