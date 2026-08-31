"""Extract a release's notes from CHANGELOG.md.

The version comes from src/videotuner/version.py and the notes from CHANGELOG.md,
which is the only place release notes are written. This lives in a script rather
than inline in the workflow so that the extraction is covered by the test suite,
instead of being exercised for the first time by the release it is meant to
produce.

Writes release_notes.md at the repository root and, under Actions, the version to
GITHUB_OUTPUT.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def extract_notes(changelog: str, version: str) -> str:
    """Return the changelog section for a version.

    Matches from the version's own `## [X.Y.Z]` heading up to whichever comes
    first: the next `## ` heading, the link definitions at the foot of the file,
    or the end of the file.

    Args:
        changelog: Full text of CHANGELOG.md
        version: Version to look for, without a leading `v`

    Returns:
        The section, stripped of surrounding blank lines.

    Raises:
        LookupError: If the file has no section for that version.
    """
    pattern = rf"## \[{re.escape(version)}\].*?(?=\n## |\n\[|\Z)"
    match = re.search(pattern, changelog, re.DOTALL)
    if match is None:
        msg = (
            f"CHANGELOG.md has no '## [{version}]' section. "
            "Promote the Unreleased heading before tagging."
        )
        raise LookupError(msg)

    return match.group(0).strip()


def main() -> None:
    """Write release_notes.md for the version the package reports."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from videotuner.version import __version__

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    try:
        notes = extract_notes(changelog, __version__)
    except LookupError as exc:
        # Fail rather than publishing a release whose notes are just its version
        # number, since a published release is awkward to take back.
        raise SystemExit(f"::error::{exc}") from exc

    _ = (REPO_ROOT / "release_notes.md").write_text(notes, encoding="utf-8")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            _ = f.write(f"version={__version__}\n")

    print(f"{__version__}: {len(notes.splitlines())} lines of release notes")


if __name__ == "__main__":
    main()
