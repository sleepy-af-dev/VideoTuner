"""Provision the local development environment at the versions a release ships.

Builds ffms2 from the commit a release pins, with .github/scripts/build-ffms2.ps1,
then installs vapoursynth-portable with its plugins, and the encoders in tools/.
The versions come from build.py and that script, so a working copy matches what
a release bundles. Each part keeps a stamp of what it was built or downloaded
from, so a re-run replaces a part whose pins changed and leaves the rest alone.

Building ffms2 needs PowerShell 7, git and Visual Studio Build Tools. Without
them, follow the manual setup in README.md, which uses the ffms2 5.0 release.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Add repo root before importing build, so this runs from any directory
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from build import (  # noqa: E402
    CHECKSUMS,
    FFMS2_FILES,
    VAPOURSYNTH_PYTHON_MINOR,
    provision_dependencies,
)

# Paths relative to the repository root
BUILD_SCRIPT = Path(".github", "scripts", "build-ffms2.ps1")
# Where this script builds ffms2: the sources and vcpkg tree in work/, vcpkg's
# binary cache in vcpkg-cache/, and the two files a release needs in out/.
# Delete it to rebuild from scratch.
FFMS2_BUILD = Path("tools", "ffms2-build")
# What a download pin change deletes. Nothing else under tools/ is touched.
DOWNLOADED = (
    Path("vapoursynth-portable"),
    Path("tools", "x264.exe"),
    Path("tools", "x265.exe"),
)

FFMS2_STAMP = "build-ffms2.sha256"
DOWNLOADS_STAMP = ".provisioned.json"

MSBUILD = "MSBuild (Visual Studio Build Tools)"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def downloads_fingerprint() -> str:
    """Identify the downloads: every pinned checksum, and the Python minor
    version the VapourSynth installer fetches."""
    return json.dumps(
        {"checksums": CHECKSUMS, "python_minor": VAPOURSYNTH_PYTHON_MINOR},
        indent=2,
        sort_keys=True,
    )


def find_msbuild() -> bool:
    """Ask vswhere for MSBuild with the query build-ffms2.ps1 uses."""
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files, "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not vswhere.exists():
        return False
    result = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.Component.MSBuild",
            "-find",
            r"MSBuild\**\Bin\MSBuild.exe",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def find_missing_tools() -> list[str]:
    """Name each tool build-ffms2.ps1 needs that this machine lacks."""
    missing = [name for name in ("pwsh", "git") if shutil.which(name) is None]
    if not find_msbuild():
        missing.append(MSBUILD)
    return missing


def run_build_script(build_dir: Path, script: Path) -> None:
    env = os.environ.copy()
    # Keep the compiled FFmpeg beside the build, unless the caller chose a cache
    _ = env.setdefault("VCPKG_DEFAULT_BINARY_CACHE", str(build_dir / "vcpkg-cache"))
    cmd = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(script),
        "-OutDir",
        str(build_dir / "out"),
        "-WorkDir",
        str(build_dir / "work"),
    ]
    result = subprocess.run(cmd, env=env, check=False)
    if result.returncode != 0:
        msg = (
            f"ERROR: build-ffms2.ps1 failed (exit {result.returncode}); "
            "its output is above"
        )
        sys.exit(msg)


def ensure_ffms2(build_dir: Path, script: Path) -> None:
    """Build ffms2 unless build_dir/out already holds a build of this script."""
    out = build_dir / "out"
    stamp = out / FFMS2_STAMP
    script_hash = file_sha256(script)
    built = all((out / name).exists() for name in FFMS2_FILES)
    if built and stamp.exists() and stamp.read_text().strip() == script_hash:
        print("ffms2 build matches build-ffms2.ps1, skipping")
        return

    missing = find_missing_tools()
    if missing:
        msg = (
            f"ERROR: cannot build ffms2, missing: {', '.join(missing)}\n"
            "Install what is missing, or follow the manual setup in README.md "
            'under "From Source", which uses the ffms2 5.0 release.'
        )
        sys.exit(msg)
    print("Building ffms2 with build-ffms2.ps1...")
    run_build_script(build_dir, script)
    # Written last, so a failed build is tried again on the next run
    _ = stamp.write_text(script_hash + "\n")


def provision(root: Path, ffms2_out: Path) -> None:
    """Install VapourSynth, its plugins and the encoders, first deleting them if
    a download pin changed since the last complete run."""
    stamp = root / "vapoursynth-portable" / DOWNLOADS_STAMP
    fingerprint = downloads_fingerprint()
    if not (stamp.exists() and stamp.read_text() == fingerprint):
        print("Download pins changed or not recorded, reinstalling them")
        for rel in DOWNLOADED:
            path = root / rel
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    provision_dependencies(root, ffms2_out)
    # Written last, so a failed or interrupted run starts clean next time
    _ = stamp.write_text(fingerprint)


def main(root: Path = REPO_ROOT) -> None:
    build_dir = root / FFMS2_BUILD
    ensure_ffms2(build_dir, root / BUILD_SCRIPT)
    provision(root, build_dir / "out")

    plugin_dir = root / "vapoursynth-portable" / "vs-plugins"
    print()
    print("Local environment ready:")
    for f in sorted(plugin_dir.iterdir()):
        print(f"  vs-plugins/{f.name}")
    for name in ("x264.exe", "x265.exe"):
        print(f"  tools/{name}")


if __name__ == "__main__":
    main()
