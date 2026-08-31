"""Tests for the release notes extraction used by the release workflow.

The script lives under .github/scripts, which is not an importable package, so it
is loaded by path.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "release_notes.py"

_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

# Loading by path leaves the module untyped, so name the one function used
extract_notes = cast("Callable[[str, str], str]", release_notes.extract_notes)


CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.5.0] - 2026-08-31

### Added

- A thing

## [0.4.1] - 2026-06-28

### Fixed

- An older thing

[Unreleased]: https://example.com/compare/v0.5.0...HEAD
[0.5.0]: https://example.com/compare/v0.4.1...v0.5.0
"""


def test_extracts_only_the_requested_section() -> None:
    notes = extract_notes(CHANGELOG, "0.5.0")

    assert notes.startswith("## [0.5.0] - 2026-08-31")
    assert "A thing" in notes
    assert "An older thing" not in notes
    assert "## [Unreleased]" not in notes
    assert "## [0.4.1]" not in notes


def test_last_section_stops_before_the_link_definitions() -> None:
    notes = extract_notes(CHANGELOG, "0.4.1")

    assert "An older thing" in notes
    assert "https://example.com" not in notes
    assert notes.endswith("An older thing")


def test_missing_section_is_reported_rather_than_defaulted() -> None:
    with pytest.raises(LookupError, match=r"no '## \[0\.6\.0\]' section"):
        _ = extract_notes(CHANGELOG, "0.6.0")


def test_version_is_matched_literally_not_as_a_pattern() -> None:
    # The dots are regex wildcards unless escaped, so this must not match 0.5.0
    with pytest.raises(LookupError):
        _ = extract_notes(CHANGELOG, "0x5x0")


def test_repository_changelog_documents_the_current_version() -> None:
    """The release fails on a version with no changelog section.

    Checking it here moves that failure to every CI run, so a version bump that
    forgets to promote the Unreleased heading is caught before a tag exists
    rather than by the release it breaks.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from videotuner.version import __version__

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    notes = extract_notes(changelog, __version__)
    assert notes.startswith(f"## [{__version__}]")
