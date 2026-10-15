"""Tests for tools/update_vs.py, the local provisioning script.

The script deletes and rebuilds parts of a working tree, so these pin what it
deletes, when it rebuilds, and that a failed run never records itself as done.
Every test runs against a tree under tmp_path with the toolchain check, the
ffms2 build and the downloads replaced, so none needs a network or Visual
Studio.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

from build import FFMS2_FILES
from tools import update_vs

Stamp = Literal["current", "stale"] | None

STALE_HASH = "0" * 64
HAND_ADDED = Path("vapoursynth-portable", "vs-plugins", "hand-added.dll")


@dataclass
class Fakes:
    """Stand-ins for the slow or machine-specific parts, and what they saw."""

    missing_tools: list[str] = field(default_factory=list)
    build_fails: bool = False
    provision_error: BaseException | None = None
    tool_checks: int = 0
    builds: list[tuple[Path, Path]] = field(default_factory=list)
    provisions: list[tuple[Path, Path]] = field(default_factory=list)


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    """Replace the toolchain check, the ffms2 build and the downloads."""
    f = Fakes()

    def fake_find_missing_tools() -> list[str]:
        f.tool_checks += 1
        return f.missing_tools

    def fake_build(build_dir: Path, script: Path) -> None:
        f.builds.append((build_dir, script))
        if f.build_fails:
            sys.exit("ERROR: build-ffms2.ps1 failed (exit 1)")
        out = build_dir / "out"
        out.mkdir(parents=True, exist_ok=True)
        for name in FFMS2_FILES:
            _ = (out / name).write_bytes(b"built")

    def fake_provision(root: Path, ffms2_dir: Path) -> None:
        f.provisions.append((root, ffms2_dir))
        if f.provision_error is not None:
            raise f.provision_error
        plugins = root / "vapoursynth-portable" / "vs-plugins"
        plugins.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(update_vs, "find_missing_tools", fake_find_missing_tools)
    monkeypatch.setattr(update_vs, "run_build_script", fake_build)
    monkeypatch.setattr(update_vs, "provision_dependencies", fake_provision)
    return f


def ffms2_stamp(root: Path) -> Path:
    return root / update_vs.FFMS2_BUILD / "out" / update_vs.FFMS2_STAMP


def downloads_stamp(root: Path) -> Path:
    return root / "vapoursynth-portable" / update_vs.DOWNLOADS_STAMP


def make_tree(root: Path, *, ffms2: Stamp, downloads: Stamp) -> None:
    """Lay out root as an earlier run would have left it.

    Args:
        root: The stand-in repository root.
        ffms2: Whether the ffms2 stamp matches the build script, is stale, or
            is missing.
        downloads: The same for the downloads stamp.
    """
    script = root / update_vs.BUILD_SCRIPT
    script.parent.mkdir(parents=True)
    _ = script.write_text("$FFMS2_COMMIT = 'pinned'\n")

    out = root / update_vs.FFMS2_BUILD / "out"
    out.mkdir(parents=True)
    for name in FFMS2_FILES:
        _ = (out / name).write_bytes(b"old")
    if ffms2 is not None:
        current = update_vs.file_sha256(script)
        _ = ffms2_stamp(root).write_text(current if ffms2 == "current" else STALE_HASH)

    (root / HAND_ADDED).parent.mkdir(parents=True)
    _ = (root / HAND_ADDED).write_bytes(b"")
    if downloads is not None:
        _ = downloads_stamp(root).write_text(
            update_vs.downloads_fingerprint() if downloads == "current" else "{}"
        )

    tools = root / "tools"
    for name in ("x264.exe", "x265.exe"):
        _ = (tools / name).write_bytes(b"")
    (tools / "FFVship_amd").mkdir()
    _ = (tools / "FFVship_amd" / "keep.txt").write_text("")


def test_nothing_changed_builds_and_deletes_nothing(
    tmp_path: Path, fakes: Fakes
) -> None:
    """Review focus: a current build needs no toolchain, so none is checked."""
    make_tree(tmp_path, ffms2="current", downloads="current")

    update_vs.main(tmp_path)

    assert fakes.builds == []
    assert fakes.tool_checks == 0
    assert (tmp_path / HAND_ADDED).exists()
    assert (tmp_path / "tools" / "x264.exe").exists()
    assert fakes.provisions == [(tmp_path, tmp_path / update_vs.FFMS2_BUILD / "out")]


def test_a_tree_from_before_stamps_settles_after_one_run(
    tmp_path: Path, fakes: Fakes
) -> None:
    """Review focus: the stamps the script writes are the ones it accepts, so
    the second run rebuilds, checks and deletes nothing."""
    make_tree(tmp_path, ffms2=None, downloads=None)

    update_vs.main(tmp_path)
    marker = tmp_path / "vapoursynth-portable" / "vs-plugins" / "marker.dll"
    _ = marker.write_bytes(b"")
    update_vs.main(tmp_path)

    assert len(fakes.builds) == 1
    assert fakes.tool_checks == 1
    assert len(fakes.provisions) == 2
    assert marker.exists()


@pytest.mark.parametrize("downloads", ["stale", None])
def test_changed_or_unrecorded_download_pins_reset_only_the_downloads(
    tmp_path: Path, fakes: Fakes, downloads: Stamp
) -> None:
    """Review focus: a pre-stamp tree resets once; other tools/ files stay."""
    make_tree(tmp_path, ffms2="current", downloads=downloads)

    update_vs.main(tmp_path)

    assert fakes.builds == []
    assert not (tmp_path / HAND_ADDED).exists()
    assert not (tmp_path / "tools" / "x264.exe").exists()
    assert not (tmp_path / "tools" / "x265.exe").exists()
    assert (tmp_path / "tools" / "FFVship_amd" / "keep.txt").exists()
    out = tmp_path / update_vs.FFMS2_BUILD / "out"
    assert all((out / name).exists() for name in FFMS2_FILES)
    assert ffms2_stamp(tmp_path).exists()
    assert downloads_stamp(tmp_path).read_text() == (update_vs.downloads_fingerprint())


@pytest.mark.parametrize("ffms2", ["stale", None])
def test_changed_or_unrecorded_build_script_rebuilds_ffms2(
    tmp_path: Path, fakes: Fakes, ffms2: Stamp
) -> None:
    """Review focus: a build with no stamp is rebuilt, since nothing says which
    pin produced it."""
    make_tree(tmp_path, ffms2=ffms2, downloads="current")
    script = tmp_path / update_vs.BUILD_SCRIPT

    update_vs.main(tmp_path)

    assert fakes.builds == [(tmp_path / update_vs.FFMS2_BUILD, script)]
    assert ffms2_stamp(tmp_path).read_text().strip() == (update_vs.file_sha256(script))
    assert (tmp_path / HAND_ADDED).exists()


def test_missing_build_output_rebuilds_despite_a_current_stamp(
    tmp_path: Path, fakes: Fakes
) -> None:
    make_tree(tmp_path, ffms2="current", downloads="current")
    (tmp_path / update_vs.FFMS2_BUILD / "out" / FFMS2_FILES[0]).unlink()

    update_vs.main(tmp_path)

    assert len(fakes.builds) == 1


def test_missing_tool_stops_before_anything_changes(
    tmp_path: Path, fakes: Fakes
) -> None:
    make_tree(tmp_path, ffms2="stale", downloads="stale")
    fakes.missing_tools = [update_vs.MSBUILD]

    with pytest.raises(SystemExit) as exc:
        update_vs.main(tmp_path)

    message = str(exc.value.code)
    assert update_vs.MSBUILD in message
    assert "README.md" in message
    assert fakes.builds == []
    assert fakes.provisions == []
    assert (tmp_path / HAND_ADDED).exists()
    assert (tmp_path / "tools" / "x264.exe").exists()


@pytest.mark.parametrize("downloads", ["stale", "current"])
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(SystemExit(1), id="failed"),
        pytest.param(KeyboardInterrupt(), id="interrupted"),
    ],
)
def test_failed_or_interrupted_provisioning_leaves_no_stamp(
    tmp_path: Path, fakes: Fakes, error: BaseException, downloads: Stamp
) -> None:
    """Review focus: the next run resets instead of trusting a half tree, even
    when the stamp matched before the run and only a hand-deleted part was
    being downloaded again."""
    make_tree(tmp_path, ffms2="current", downloads=downloads)
    fakes.provision_error = error

    with pytest.raises(type(error)):
        update_vs.main(tmp_path)

    assert not downloads_stamp(tmp_path).exists()


def test_failed_build_keeps_the_old_stamp_and_provisions_nothing(
    tmp_path: Path, fakes: Fakes
) -> None:
    make_tree(tmp_path, ffms2="stale", downloads="current")
    fakes.build_fails = True

    with pytest.raises(SystemExit):
        update_vs.main(tmp_path)

    assert ffms2_stamp(tmp_path).read_text() == STALE_HASH
    assert fakes.provisions == []


@pytest.mark.parametrize("preset", [None, "D:/vcpkg-cache"])
def test_build_keeps_its_cache_beside_the_build_unless_one_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preset: str | None
) -> None:
    """Review focus: a caller's own VCPKG_DEFAULT_BINARY_CACHE is honoured."""
    if preset is None:
        monkeypatch.delenv("VCPKG_DEFAULT_BINARY_CACHE", raising=False)
    else:
        monkeypatch.setenv("VCPKG_DEFAULT_BINARY_CACHE", preset)
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        cmd: list[str], *, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert not check
        seen.append((cmd, env))
        return subprocess.CompletedProcess[bytes](cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    build_dir = tmp_path / "tools" / "ffms2-build"
    script = tmp_path / "build-ffms2.ps1"

    update_vs.run_build_script(build_dir, script)

    assert len(seen) == 1
    cmd, env = seen[0]
    assert cmd == [
        "pwsh",
        "-NoProfile",
        "-File",
        str(script),
        "-OutDir",
        str(build_dir / "out"),
        "-WorkDir",
        str(build_dir / "work"),
    ]
    expected = str(build_dir / "vcpkg-cache") if preset is None else preset
    assert env["VCPKG_DEFAULT_BINARY_CACHE"] == expected


def test_build_failure_stops_with_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(
        cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess[bytes](cmd, 3)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        update_vs.run_build_script(tmp_path, tmp_path / "build-ffms2.ps1")

    assert "exit 3" in str(exc.value.code)


def test_find_missing_tools_names_each_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return None if name == "git" else f"C:/bin/{name}.exe"

    def no_msbuild() -> bool:
        return False

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(update_vs, "find_msbuild", no_msbuild)

    assert update_vs.find_missing_tools() == ["git", update_vs.MSBUILD]
