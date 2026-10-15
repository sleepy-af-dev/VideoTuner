"""Provision the local development environment using versions from build.py.

Installs vapoursynth-portable with its plugins and the encoders in tools/, which
together are everything the pipeline needs to run from a source checkout. The
versions come from build.py, so a local environment matches what a release
bundles rather than drifting from it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add repo root before importing build, so this runs from any directory
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from build import (  # noqa: E402
    FFMS2_FILES,
    download_ffms2,
    download_lsmashsource,
    download_vstonemap,
    download_vszip,
    download_x264,
    download_x265,
    install_ffms2_from,
    install_vapoursynth_portable,
    verify_provisioned,
)

VS_DIR = REPO_ROOT / "vapoursynth-portable"
TOOLS_DIR = REPO_ROOT / "tools"
# Where build-ffms2.ps1 leaves its output when run with the documented paths
FFMS2_BUILD_OUT = TOOLS_DIR / "ffms2-build" / "out"


def main() -> None:
    install_vapoursynth_portable(VS_DIR)

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    download_x264(TOOLS_DIR)
    download_x265(TOOLS_DIR)

    plugin_dir = VS_DIR / "vs-plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Prefer a local ffms2 build, which is what a release bundles. Without one,
    # take the 5.0 download, which is what a manual setup gets.
    if all((FFMS2_BUILD_OUT / name).exists() for name in FFMS2_FILES):
        install_ffms2_from(FFMS2_BUILD_OUT, plugin_dir)
    else:
        print("  No local ffms2 build found, using the 5.0 release")
        download_ffms2(plugin_dir)

    download_lsmashsource(plugin_dir)
    download_vszip(plugin_dir)
    download_vstonemap(plugin_dir)

    verify_provisioned(REPO_ROOT)

    print()
    print("Local environment ready:")
    for f in sorted(plugin_dir.iterdir()):
        print(f"  vs-plugins/{f.name}")
    for name in ("x264.exe", "x265.exe"):
        print(f"  tools/{name}")


if __name__ == "__main__":
    main()
