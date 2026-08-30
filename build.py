"""Build script for creating VideoTuner releases with Nuitka."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

# Import version from the package
sys.path.insert(0, str(Path(__file__).parent / "src"))
from videotuner.version import __version__

REPO_ROOT = Path(__file__).parent
DIST_DIR = REPO_ROOT / "dist"
RELEASE_NAME = f"VideoTuner-v{__version__}"
RELEASE_DIR = DIST_DIR / RELEASE_NAME

# External dependency URLs and versions
# x264 encoder
X264_VERSION = "0.165.3223+40"
X264_URL = "https://github.com/Patman86/x264-Mod-by-Patman/releases/download/0.165.3223%2B40/x264-0.165.3223+40-25a99de-.Mod-by-Patman.-x64-clang22.1.8.7z"

# x265 encoder. The avx2 build already contains the AVX-512 assembly kernels;
# the tag names the compiler target for the C/C++ code, so an avx512 build only
# raises the CPU floor without adding anything x265 uses by default.
X265_VERSION = "4.2+68+68"
X265_URL = "https://github.com/Patman86/x265-Mod-by-Patman/releases/download/4.2%2B68%2B68/x265-4.2+68+68-2df85ea68-.Mod-by-Patman.-x64-avx2-clang22.1.8.7z"

# VapourSynth portable environment. Since R74 the installer ships inside a zip
# rather than as a bare .ps1, and the portable tree is built by pip installing a
# wheel, so VapourSynth's own binaries live under Lib/site-packages/vapoursynth.
VAPOURSYNTH_VERSION = "R79"
VAPOURSYNTH_INSTALLER_URL = f"https://github.com/vapoursynth/vapoursynth/releases/download/{VAPOURSYNTH_VERSION}/Install-Portable-VapourSynth-{VAPOURSYNTH_VERSION}.zip"

# Python bundled into the portable environment. Independent of the Python this
# project runs under: it only executes the generated .vpy scripts.
VAPOURSYNTH_PYTHON_MINOR = 14

# Where the wheel puts VapourSynth inside the portable tree
VS_PACKAGE_SUBDIR = "Lib/site-packages/vapoursynth"

# VapourSynth plugins (all x64)
FFMS2_VERSION = "5.0"
FFMS2_URL = f"https://github.com/FFMS/ffms2/releases/download/{FFMS2_VERSION}/ffms2-{FFMS2_VERSION}-msvc.7z"

LSMASH_VERSION = "1310.0.0.0"
LSMASH_URL = f"https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works/releases/download/{LSMASH_VERSION}/L-SMASH-Works-r{LSMASH_VERSION}.7z"

# vszip stopped attaching binaries to its GitHub releases after R13 and now
# publishes to PyPI only, so this is a wheel rather than a release asset. vsrepo
# has not tracked it either and still lists R13 as newest.
VSZIP_VERSION = "22.1.0"
VSZIP_URL = f"https://files.pythonhosted.org/packages/py3/v/vapoursynth-vszip/vapoursynth_vszip-{VSZIP_VERSION}-py3-none-win_amd64.whl"

# The wheel carries a directory of per-CPU builds plus a manifest that tells
# VapourSynth which to load, so the whole directory is installed rather than one
# DLL. Extracting a single file would give every machine the baseline build.
VSZIP_WHEEL_SUBDIR = "vapoursynth/plugins/vszip/"
VSZIP_DIR = "vszip"

# SHA256 checksums for integrity verification (protects against compromised downloads)
# To update: download file, run: python -c "import hashlib; print(hashlib.sha256(open('file','rb').read()).hexdigest())"  # noqa: E501  # TODO(E501): shorten line
CHECKSUMS = {
    "vapoursynth_installer": "ee5bb140137ad5321e03b5421670331483cf50eea20de3086b9620f76e439d7f",  # noqa: E501  # TODO(E501): shorten line
    "x264": "e79043b415030e189bbfaba4a33b83fdebb0000876fe25a3a306b0da98782c49",
    "x265": "53c2da979f2b66204b7bfbd7074356a59ee7bac14bffa3d3ad5cbff066e19200",
    "ffms2": "e867a3df7262865107df40f230f5b8e1455905eba9b8852e6f35b1227537caeb",
    "lsmash": "b4fd48e3cb97c9583e08e69a3dd49a6500dede3bbc054f0fb9cddd0f5448e6e1",
    "vszip": "1f5e6aa39ea72e610c1146c2d27f157bea507895555398c60347a65ee1745273",
}


def verify_checksum(file_path: Path, expected_hash: str, name: str) -> None:
    """Verify SHA256 checksum of a downloaded file.

    Args:
        file_path: Path to the file to verify
        expected_hash: Expected SHA256 hex digest
        name: Human-readable name for error messages

    Raises:
        SystemExit: If checksum doesn't match
    """
    if expected_hash == "PLACEHOLDER":
        # Skip verification if hash not yet set - print actual hash for user to add
        actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
        print(f"  WARNING: No checksum for {name}. Actual SHA256: {actual}")
        return

    actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if actual != expected_hash:
        print(f"ERROR: Checksum mismatch for {name}!")
        print(f"  Expected: {expected_hash}")
        print(f"  Actual:   {actual}")
        print("This could indicate a compromised or corrupted download.")
        sys.exit(1)


# Nuitka names its working directories after the package it compiles, which is
# src/videotuner, giving videotuner.build, videotuner.dist and
# videotuner.onefile-build alongside the release folder in dist/
NUITKA_PACKAGE = "videotuner"


def clean_previous_build() -> None:
    """Remove previous build artifacts."""
    if RELEASE_DIR.exists():
        print(f"Cleaning previous release: {RELEASE_DIR}")
        shutil.rmtree(RELEASE_DIR)

    for path in sorted(DIST_DIR.glob(f"{NUITKA_PACKAGE}.*")):
        if path.is_dir():
            print(f"Cleaning Nuitka working directory: {path.name}")
            shutil.rmtree(path)


PYTHON_PROBE_CAP = 30


def patch_vs_installer(script: str) -> tuple[str, list[str], list[str]]:
    """Apply compatibility patches to the VapourSynth portable installer script.

    Fixes known issues in the upstream installer that affect non-interactive use:
    - Adds -UseBasicParsing to Invoke-WebRequest calls (Windows PowerShell 5.1's
      IE DOM parser fails in -NonInteractive mode)
    - Raises the Python patch version probe limit, which upstream sets just above
      the newest release at the time and which silently caps the Python version
      installed once the interpreter gains more patch releases

    Returns:
        Tuple of (patched script, applied descriptions, skipped descriptions).
        A patch is skipped when its target pattern is absent, which means the
        upstream script has changed and the patch needs revisiting.
    """
    applied: list[str] = []
    skipped: list[str] = []

    # Fix 1: Add -UseBasicParsing to all Invoke-WebRequest calls
    iwr_pattern = re.compile(r"Invoke-WebRequest\b(?!.*-UseBasicParsing)")
    if iwr_pattern.search(script):
        script = iwr_pattern.sub("Invoke-WebRequest -UseBasicParsing", script)
        applied.append("Added -UseBasicParsing to Invoke-WebRequest calls")
    else:
        skipped.append("-UseBasicParsing on Invoke-WebRequest")

    # Fix 2: Raise the Python patch version probe limit. Matched by structure
    # rather than by literal, because upstream moves the cap every release.
    probe_pattern = re.compile(r"(\$i = \$PythonVersionPatch \+ 1; \$i -le )(\d+)")
    probe_match = probe_pattern.search(script)
    if probe_match and int(probe_match.group(2)) < PYTHON_PROBE_CAP:
        script = probe_pattern.sub(rf"\g<1>{PYTHON_PROBE_CAP}", script, count=1)
        applied.append(
            f"Raised Python patch version probe limit "
            f"from {probe_match.group(2)} to {PYTHON_PROBE_CAP}"
        )
    elif not probe_match:
        skipped.append("Python patch version probe limit")

    return script, applied, skipped


def install_vapoursynth_portable(target_dir: Path) -> None:
    """Download and run VapourSynth portable installer.

    Downloads the official installer script from GitHub, applies compatibility
    patches for non-interactive use, and executes it to create a portable
    VapourSynth environment at the target directory.
    """
    if target_dir.exists():
        print(f"  VapourSynth portable already exists at {target_dir}, skipping")
        return

    print(f"Installing VapourSynth {VAPOURSYNTH_VERSION} portable environment...")

    with tempfile.TemporaryDirectory() as tmpdir:
        script_name = f"Install-Portable-VapourSynth-{VAPOURSYNTH_VERSION}.ps1"
        archive_path = Path(tmpdir) / f"{script_name}.zip"
        installer_path = Path(tmpdir) / script_name

        # Download the installer archive
        print(f"  Downloading installer from {VAPOURSYNTH_INSTALLER_URL}")
        try:
            _ = urllib.request.urlretrieve(VAPOURSYNTH_INSTALLER_URL, archive_path)
        except Exception as e:
            print(f"ERROR: Failed to download VapourSynth installer: {e}")
            sys.exit(1)

        verify_checksum(
            archive_path, CHECKSUMS["vapoursynth_installer"], "VapourSynth installer"
        )

        # The archive holds the .ps1 alongside a .bat wrapper we do not use
        try:
            with zipfile.ZipFile(archive_path) as zf:
                _ = zf.extract(script_name, tmpdir)
        except (zipfile.BadZipFile, KeyError) as e:
            print(f"ERROR: Could not extract {script_name} from installer archive: {e}")
            sys.exit(1)

        # Patch the installer for non-interactive compatibility
        original = installer_path.read_text(encoding="utf-8")
        patched, applied_patches, skipped_patches = patch_vs_installer(original)
        _ = installer_path.write_text(patched, encoding="utf-8")
        for patch_desc in applied_patches:
            print(f"  Patched installer: {patch_desc}")
        for patch_desc in skipped_patches:
            print(
                f"  WARNING: patch no longer applies ({patch_desc}) — "
                "upstream script has changed"
            )

        # Run the PowerShell installer with target folder
        # The installer creates the folder relative to its working directory,
        # so we run it from the parent of the target and specify the folder name
        target_parent = target_dir.parent
        target_name = target_dir.name

        cmd = [
            "powershell.exe",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer_path),
            "-TargetFolder",
            target_name,
            "-PythonVersionMinor",
            str(VAPOURSYNTH_PYTHON_MINOR),
            "-Unattended",
        ]

        print("  Running installer (this may take a minute)...")
        result = subprocess.run(cmd, cwd=target_parent)

        if result.returncode != 0:
            print(f"ERROR: VapourSynth installer failed with code {result.returncode}")
            sys.exit(1)

        if not target_dir.exists():
            print(f"ERROR: VapourSynth installation did not create {target_dir}")
            sys.exit(1)

        print(f"  VapourSynth {VAPOURSYNTH_VERSION} installed to {target_dir}")


def download_vszip(plugin_dir: Path) -> None:
    """Download vszip and install its plugin directory into plugin_dir."""
    dest_dir = plugin_dir / VSZIP_DIR
    if dest_dir.exists():
        print(f"  {VSZIP_DIR}/ already exists, skipping download")
        return

    print(f"Downloading vszip {VSZIP_VERSION}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        wheel_path = Path(tmpdir) / "vszip.whl"

        # Download the wheel
        try:
            _ = urllib.request.urlretrieve(VSZIP_URL, wheel_path)
        except Exception as e:
            print(f"ERROR: Failed to download vszip: {e}")
            sys.exit(1)

        verify_checksum(wheel_path, CHECKSUMS["vszip"], "vszip")

        # Copy the plugin directory out of the wheel, manifest included
        try:
            with zipfile.ZipFile(wheel_path, "r") as zf:
                members = [
                    n
                    for n in zf.namelist()
                    if n.startswith(VSZIP_WHEEL_SUBDIR) and not n.endswith("/")
                ]
                if not members:
                    print(f"ERROR: {VSZIP_WHEEL_SUBDIR} not found in vszip wheel")
                    sys.exit(1)

                dest_dir.mkdir(parents=True)
                for name in members:
                    with (
                        zf.open(name) as src,
                        (dest_dir / Path(name).name).open("wb") as dst,
                    ):
                        _ = shutil.copyfileobj(src, dst)

                print(f"  Extracted {len(members)} vszip files to {dest_dir}")

        except zipfile.BadZipFile as e:
            print(f"ERROR: Invalid wheel file: {e}")
            sys.exit(1)


def _bsdtar() -> Path:
    """Locate the bsdtar that ships with Windows.

    Called by full path rather than by name so a GNU tar earlier on PATH (Git
    for Windows installs one) cannot be picked up instead. GNU tar cannot read
    7z archives, and libarchive can.
    """
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if not tar.exists():
        print(f"ERROR: bsdtar not found at {tar} (requires Windows 10 or later)")
        sys.exit(1)
    return tar


def extract_from_7z(
    archive_path: Path,
    files_to_extract: list[str],
    dest_dir: Path,
) -> None:
    """Extract specific files from a 7z archive.

    Args:
        archive_path: Path to the .7z archive
        files_to_extract: List of file paths within the archive to extract
        dest_dir: Destination directory for extracted files
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # bsdtar keeps the archive's directory structure, so each file lands at
        # its own path below tmpdir rather than flattened into the root
        cmd = [
            str(_bsdtar()),
            "-xf",
            str(archive_path),
            "-C",
            tmpdir,
            *files_to_extract,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR: extraction failed: {result.stderr}")
            sys.exit(1)

        # Move extracted files to destination
        for file_path in files_to_extract:
            src = Path(tmpdir) / file_path
            if src.exists():
                _ = shutil.copy2(src, dest_dir / src.name)
            else:
                print(f"ERROR: Expected file not found after extraction: {file_path}")
                sys.exit(1)


FFMS2_FILES = ("ffms2.dll", "ffmsindex.exe")


def install_ffms2_from(source_dir: Path, plugin_dir: Path) -> None:
    """Install a locally built ffms2 instead of downloading the release.

    ffms2 has published no binary since 5.0, so a release bundles one built from
    a later commit. Verified as strictly as a download would be: the files have
    to be there, and there is no fallback to the older archive if they are not.
    """
    print(f"Installing ffms2 from {source_dir}...")
    missing = [name for name in FFMS2_FILES if not (source_dir / name).exists()]
    if missing:
        print(f"ERROR: {source_dir} is missing {', '.join(missing)}")
        sys.exit(1)

    for name in FFMS2_FILES:
        _ = shutil.copy2(source_dir / name, plugin_dir / name)
    print(f"  Copied {' and '.join(FFMS2_FILES)} to {plugin_dir}")


def download_ffms2(plugin_dir: Path) -> None:
    """Download and extract ffms2 plugin."""
    dest_dll = plugin_dir / "ffms2.dll"
    if dest_dll.exists():
        print("  ffms2.dll already exists, skipping download")
        return

    print(f"Downloading ffms2 {FFMS2_VERSION}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = Path(tmpdir) / "ffms2.7z"

        try:
            _ = urllib.request.urlretrieve(FFMS2_URL, archive_path)
        except Exception as e:
            print(f"ERROR: Failed to download ffms2: {e}")
            sys.exit(1)

        verify_checksum(archive_path, CHECKSUMS["ffms2"], "ffms2")

        # Extract ffms2.dll and ffmsindex.exe from x64 folder
        extract_from_7z(
            archive_path,
            [
                f"ffms2-{FFMS2_VERSION}-msvc/x64/ffms2.dll",
                f"ffms2-{FFMS2_VERSION}-msvc/x64/ffmsindex.exe",
            ],
            plugin_dir,
        )
        print(f"  Extracted ffms2.dll and ffmsindex.exe to {plugin_dir}")


def download_lsmashsource(plugin_dir: Path) -> None:
    """Download and extract LSMASHSource plugin."""
    dest_dll = plugin_dir / "LSMASHSource.dll"
    if dest_dll.exists():
        print("  LSMASHSource.dll already exists, skipping download")
        return

    print(f"Downloading LSMASHSource {LSMASH_VERSION}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = Path(tmpdir) / "lsmash.7z"

        try:
            _ = urllib.request.urlretrieve(LSMASH_URL, archive_path)
        except Exception as e:
            print(f"ERROR: Failed to download LSMASHSource: {e}")
            sys.exit(1)

        verify_checksum(archive_path, CHECKSUMS["lsmash"], "LSMASHSource")

        # Extract LSMASHSource.dll from x64 folder
        extract_from_7z(
            archive_path,
            ["x64/LSMASHSource.dll"],
            plugin_dir,
        )
        print(f"  Extracted LSMASHSource.dll to {plugin_dir}")


def download_x264(tools_dir: Path) -> None:
    """Download and extract x264 encoder."""
    dest_exe = tools_dir / "x264.exe"
    if dest_exe.exists():
        print("  x264.exe already exists, skipping download")
        return

    print(f"Downloading x264 {X264_VERSION}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = Path(tmpdir) / "x264.7z"

        try:
            _ = urllib.request.urlretrieve(X264_URL, archive_path)
        except Exception as e:
            print(f"ERROR: Failed to download x264: {e}")
            sys.exit(1)

        verify_checksum(archive_path, CHECKSUMS["x264"], "x264")

        # Extract x264.exe from archive root
        extract_from_7z(
            archive_path,
            ["x264.exe"],
            tools_dir,
        )
        print(f"  Extracted x264.exe to {tools_dir}")


def download_x265(tools_dir: Path) -> None:
    """Download and extract x265 encoder."""
    dest_exe = tools_dir / "x265.exe"
    if dest_exe.exists():
        print("  x265.exe already exists, skipping download")
        return

    print(f"Downloading x265 {X265_VERSION}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = Path(tmpdir) / "x265.7z"

        try:
            _ = urllib.request.urlretrieve(X265_URL, archive_path)
        except Exception as e:
            print(f"ERROR: Failed to download x265: {e}")
            sys.exit(1)

        verify_checksum(archive_path, CHECKSUMS["x265"], "x265")

        # Extract x265.exe from archive root
        extract_from_7z(
            archive_path,
            ["x265.exe"],
            tools_dir,
        )
        print(f"  Extracted x265.exe to {tools_dir}")


def run_nuitka() -> Path:
    """Run Nuitka to build the executable."""
    print("Building with Nuitka (this may take several minutes)...")

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--assume-yes-for-downloads",  # Auto-accept dependency downloads in CI
        f"--output-dir={DIST_DIR}",
        "--output-filename=VideoTuner.exe",
        # Compile as a package run with -m (uses __main__.py automatically)
        "--python-flag=-m",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=tests",
        "--include-package=rich._unicode_data",  # Required for rich text rendering
        "--windows-console-mode=force",
        # Optional: Add version info to the exe
        f"--product-version={__version__}",
        f"--file-version={__version__}",
        "--product-name=VideoTuner",
        "--company-name=sleepy-af-dev",
        "--copyright=Copyright 2025 sleepy-af-dev",
        "--file-description=CRF optimization and encoder benchmarking tool",
        # Point to the package directory (not __main__.py)
        "src/videotuner",
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)

    if result.returncode != 0:
        print("Nuitka build failed!")
        sys.exit(1)

    # Nuitka names output after source file when using --output-filename
    exe_path = DIST_DIR / "VideoTuner.exe"
    if not exe_path.exists():
        # Fallback: check alternate names if --output-filename didn't work
        for alt_name in ["videotuner.exe", "__main__.exe"]:
            alt_path = DIST_DIR / alt_name
            if alt_path.exists():
                _ = alt_path.rename(exe_path)
                break
        else:
            print(f"Expected exe not found at: {exe_path}")
            print("Checked: VideoTuner.exe, videotuner.exe, __main__.exe")
            sys.exit(1)

    return exe_path


# Paths the application resolves at runtime (see VapourSynthEnv in
# encoding_utils.py). Provisioning checks these itself so a layout change
# upstream fails the build rather than shipping a release that cannot start.
REQUIRED_VAPOURSYNTH = (
    "vapoursynth-portable/python.exe",
    f"vapoursynth-portable/{VS_PACKAGE_SUBDIR}/vspipe.exe",
    f"vapoursynth-portable/{VS_PACKAGE_SUBDIR}/vsscript.dll",
    f"vapoursynth-portable/{VS_PACKAGE_SUBDIR}/libvapoursynth.dll",
    "vapoursynth-portable/vs-plugins/ffms2.dll",
    "vapoursynth-portable/vs-plugins/ffmsindex.exe",
    "vapoursynth-portable/vs-plugins/LSMASHSource.dll",
    "vapoursynth-portable/vs-plugins/vszip/vszip.dll",
    "vapoursynth-portable/vs-plugins/vszip/manifest.vs",
)

REQUIRED_ENCODERS = (
    "tools/x264.exe",
    "tools/x265.exe",
)

REQUIRED_AFTER_PROVISION = REQUIRED_VAPOURSYNTH + REQUIRED_ENCODERS


def verify_provisioned(
    dest: Path, required: Sequence[str] = REQUIRED_AFTER_PROVISION
) -> None:
    """Check every file the application expects is present under dest.

    The download helpers each verify their own output, but nothing otherwise
    confirms the VapourSynth installer produced the layout the app resolves
    against. Raises SystemExit listing all missing paths.

    Args:
        dest: Root the paths are relative to
        required: Paths to check. Narrow this to the subset the caller actually
            provisioned, so a missing piece nobody promised is not reported as
            a failure.
    """
    missing = [rel for rel in required if not (dest / rel).exists()]
    if missing:
        print(f"ERROR: provisioning left {len(missing)} required path(s) missing:")
        for rel in missing:
            print(f"  {dest / rel}")
        sys.exit(1)
    print(f"  Verified {len(required)} required paths")


def provision_dependencies(dest: Path, ffms2_dir: Path | None = None) -> None:
    """Download VapourSynth, its plugins and the encoders into dest.

    Args:
        dest: Root to provision into
        ffms2_dir: Directory holding a locally built ffms2. When given, it is
            used instead of the 5.0 download.
    """
    # The VapourSynth installer runs with dest as its working directory
    dest.mkdir(parents=True, exist_ok=True)

    # Install vapoursynth-portable from official source
    vs_dst = dest / "vapoursynth-portable"
    install_vapoursynth_portable(vs_dst)

    # Download encoders to tools folder
    tools_dst = dest / "tools"
    tools_dst.mkdir(parents=True, exist_ok=True)
    download_x264(tools_dst)
    download_x265(tools_dst)

    # Download plugins to the plugin directory. Since R74 the installer no
    # longer creates this, and it is reached via VAPOURSYNTH_EXTRA_PLUGIN_PATH
    # rather than by sitting beside portable.vs.
    plugin_dir = vs_dst / "vs-plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if ffms2_dir is not None:
        install_ffms2_from(ffms2_dir, plugin_dir)
    else:
        download_ffms2(plugin_dir)
    download_lsmashsource(plugin_dir)
    download_vszip(plugin_dir)

    verify_provisioned(dest)


def assemble_release(exe_path: Path, ffms2_dir: Path | None = None) -> None:
    """Assemble the release folder with exe and required files."""
    print(f"Assembling release: {RELEASE_DIR}")

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy the executable
    _ = shutil.copy2(exe_path, RELEASE_DIR / "VideoTuner.exe")

    provision_dependencies(RELEASE_DIR, ffms2_dir)

    # Copy sample profile config
    sample_config = REPO_ROOT / "profiles.yaml.sample"
    if sample_config.exists():
        _ = shutil.copy2(sample_config, RELEASE_DIR / "profiles.yaml.sample")

    # Copy README
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        _ = shutil.copy2(readme, RELEASE_DIR / "README.md")

    # Copy license files
    for license_file in ["LICENSE", "THIRD_PARTY_LICENSES.md"]:
        src = REPO_ROOT / license_file
        if src.exists():
            _ = shutil.copy2(src, RELEASE_DIR / license_file)

    # Copy licenses folder (third-party license texts)
    licenses_src = REPO_ROOT / "licenses"
    licenses_dst = RELEASE_DIR / "licenses"
    if licenses_src.exists():
        print("Copying licenses/ ...")
        _ = shutil.copytree(licenses_src, licenses_dst)
    else:
        print(f"WARNING: licenses/ not found at {licenses_src}")

    # Clean up the standalone exe from dist root (now in release folder)
    exe_path.unlink()


def print_summary() -> None:
    """Print build summary."""
    print()
    print("=" * 60)
    print(f"BUILD COMPLETE: {RELEASE_NAME}")
    print("=" * 60)
    print()
    print(f"Release folder: {RELEASE_DIR}")
    print()
    print("Contents:")
    for item in sorted(RELEASE_DIR.iterdir()):
        if item.is_dir():
            # Count files in directory
            file_count = sum(1 for _ in item.rglob("*") if _.is_file())
            print(f"  {item.name}/  ({file_count} files)")
        else:
            size_mb = item.stat().st_size / (1024 * 1024)
            print(f"  {item.name}  ({size_mb:.1f} MB)")
    print()


def main() -> None:
    """Main build entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--deps-only",
        action="store_true",
        help=(
            "Download and verify the bundled dependencies without compiling. "
            "Exercises every external URL, checksum and archive layout."
        ),
    )
    _ = parser.add_argument(
        "--ffms2-dir",
        type=Path,
        metavar="PATH",
        help=(
            "Use a locally built ffms2 from PATH (ffms2.dll and ffmsindex.exe) "
            "instead of downloading the 5.0 release, which is the newest binary "
            "published."
        ),
    )
    args = parser.parse_args()

    if args.deps_only:
        print(f"Provisioning VideoTuner v{__version__} dependencies (no compile)")
        print()
        provision_dependencies(RELEASE_DIR, args.ffms2_dir)
        return

    print(f"Building VideoTuner v{__version__}")
    print()

    clean_previous_build()
    exe_path = run_nuitka()
    assemble_release(exe_path, args.ffms2_dir)
    print_summary()


if __name__ == "__main__":
    main()
