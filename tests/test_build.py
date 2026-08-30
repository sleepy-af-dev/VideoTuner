"""Tests for the release build script's installer patching.

The patches are applied to an upstream PowerShell script that changes shape
between VapourSynth releases. A patch that stops matching is only reported as a
warning, so nothing else fails when one silently rots.
"""

from __future__ import annotations

from pathlib import Path

from build import PYTHON_PROBE_CAP, VS_PACKAGE_SUBDIR, patch_vs_installer
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
