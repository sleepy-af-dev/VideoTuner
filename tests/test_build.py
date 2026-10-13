"""Tests for the release build script's installer patching.

The patches are applied to an upstream PowerShell script that changes shape
between VapourSynth releases. A patch that stops matching is only reported as a
warning, so nothing else fails when one silently rots.
"""

from __future__ import annotations

import contextlib
import inspect
import re
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

import build
from build import CHECKSUMS, PYTHON_PROBE_CAP, VS_PACKAGE_SUBDIR, patch_vs_installer
from videotuner.encoding_utils import VS_PACKAGE_SUBDIR as RUNTIME_VS_PACKAGE_SUBDIR

# The probe loop as it appears upstream. R73 capped it at 10, R79 at 15, which
# is why this is matched by structure rather than by literal.
PROBE_R73 = "for ($i = $PythonVersionPatch + 1; $i -le 10; $i++) {"
PROBE_R79 = "for ($i = $PythonVersionPatch + 1; $i -le 15; $i++) {"

FETCH = "Invoke-WebRequest -Uri $PyUri -Method head"


def test_build_and_runtime_agree_on_where_vapoursynth_lives() -> None:
    """The build verifies paths the app later resolves, so these cannot drift.

    build.py stays free of package imports so it can run before an install, so
    the constant is declared twice. This is what stops the copies diverging.
    """
    assert Path(VS_PACKAGE_SUBDIR) == RUNTIME_VS_PACKAGE_SUBDIR


def test_probe_cap_raised_for_both_known_upstream_forms() -> None:
    for original in (PROBE_R73, PROBE_R79):
        patched, applied, skipped = patch_vs_installer(original)

        assert f"-le {PYTHON_PROBE_CAP};" in patched
        assert any("probe limit" in desc for desc in applied)
        assert "Python patch version probe limit" not in skipped


def test_missing_probe_loop_is_reported_as_skipped() -> None:
    _, applied, skipped = patch_vs_installer(FETCH)

    assert "Python patch version probe limit" in skipped
    assert not any("probe limit" in desc for desc in applied)


def test_probe_cap_left_alone_when_already_high_enough() -> None:
    already = f"for ($i = $PythonVersionPatch + 1; $i -le {PYTHON_PROBE_CAP}; $i++) {{"
    patched, applied, skipped = patch_vs_installer(already)

    assert patched == already
    assert not any("probe limit" in desc for desc in applied)
    # Present but already sufficient is not a sign upstream has changed
    assert "Python patch version probe limit" not in skipped


def test_basic_parsing_added_once_and_only_when_missing() -> None:
    patched, applied, _ = patch_vs_installer(FETCH)
    assert patched.count("-UseBasicParsing") == 1
    assert any("UseBasicParsing" in desc for desc in applied)

    # Running over an already-patched script must not double up
    again, applied_again, skipped_again = patch_vs_installer(patched)
    assert again.count("-UseBasicParsing") == 1
    assert not applied_again
    assert "-UseBasicParsing on Invoke-WebRequest" in skipped_again


def test_every_bundled_download_has_a_real_checksum() -> None:
    """No pin is left disabled.

    verify_checksum treats "PLACEHOLDER" as permission to skip, printing the
    hash it saw and carrying on. That is useful while adding a dependency and
    silent once forgotten, which would leave a download unverified without
    anything failing.
    """
    for name, digest in CHECKSUMS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{name} has no SHA256: {digest}"


_MODULE: dict[str, object] = dict(vars(build))

#: Every download function, found rather than listed, so one added later is
#: covered by the guard test below without anyone remembering to add it.
DOWNLOADERS: list[tuple[str, Callable[[Path], None]]] = [
    (name, cast("Callable[[Path], None]", member))
    for name, member in sorted(_MODULE.items())
    if name.startswith("download_") and inspect.isfunction(member)
]


def _run_downloader(
    fn: Callable[[Path], None],
    dest: Path,
    present: Sequence[str],
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Run one download function with the network and extraction stubbed.

    Args:
        fn: The download function under test.
        dest: Directory it installs into. Created if it does not exist.
        present: Files placed there first, standing in for what an earlier
            provisioning run left behind.
        monkeypatch: Used to replace the download and extraction calls.

    Returns:
        The basenames the function extracted, empty when its guard skipped the
        download or when it does not extract from a 7z archive at all.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for name in present:
        _ = (dest / name).write_bytes(b"")

    extracted: list[str] = []

    def fake_extract(_archive: Path, files: list[str], dest_dir: Path) -> None:
        for member in files:
            extracted.append(Path(member).name)
            _ = (dest_dir / Path(member).name).write_bytes(b"")

    def fake_urlretrieve(_url: str, filename: Path) -> tuple[Path, None]:
        _ = Path(filename).write_bytes(b"")
        return Path(filename), None

    def fake_verify(_path: Path, _digest: str, _name: str) -> None:
        return None

    monkeypatch.setattr(build, "extract_from_7z", fake_extract)
    monkeypatch.setattr(build, "verify_checksum", fake_verify)
    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)

    # A function that installs a whole directory opens the archive itself and
    # exits when the stub file turns out not to be one. That leaves nothing
    # extracted, which is what skips it below.
    with contextlib.suppress(SystemExit):
        fn(dest)

    return extracted


@pytest.mark.parametrize(("name", "fn"), DOWNLOADERS)
def test_download_guard_covers_every_file_it_extracts(
    name: str,
    fn: Callable[[Path], None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard that tests fewer files than it installs leaves one missing.

    Each of these skips the download when its files are already there. Testing
    one file of several means an install provisioned before the second file was
    added is treated as complete, and the missing one never arrives. The trap
    has been walked into twice, so this measures the guard rather than trusting
    it: run the function once to learn what it extracts, then again with each
    of those files missing in turn.
    """
    files = _run_downloader(fn, tmp_path / name, (), monkeypatch)
    if len(files) < 2:
        pytest.skip(f"{name} does not extract more than one file")

    for missing in files:
        rest = [f for f in files if f != missing]
        again = _run_downloader(
            fn, tmp_path / f"{name}-without-{missing}", rest, monkeypatch
        )
        assert again, (
            f"{name} skipped its download with {missing} missing, so its guard"
            " tests fewer files than it installs"
        )
