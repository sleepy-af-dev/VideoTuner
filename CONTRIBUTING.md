# Contributing to VideoTuner

Thank you for your interest in contributing to VideoTuner! This document provides guidelines and instructions for contributing.

## Code of Conduct

This project has a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold it. Please report unacceptable behavior as described there.

## Getting Started

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- External tools on PATH: FFmpeg (with libvmaf), ffprobe, mkvmerge

### Development Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/sleepy-af-dev/VideoTuner.git
   cd VideoTuner
   ```

2. **Install with development dependencies:**

   Using uv (recommended):

   ```bash
   uv sync --extra dev
   ```

   Using pip:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

3. **Provision VapourSynth, its plugins and the encoders:**

   ```bash
   uv run python tools/update_vs.py
   ```

   This installs `vapoursynth-portable/` with its plugins, and `x264.exe` and `x265.exe` in `tools/`, at the versions `build.py` pins for a release. ffms2 has published no binary since 5.0, so the script builds the same ffms2 a release bundles, using `.github/scripts/build-ffms2.ps1`. That build needs:

   - PowerShell 7 (`pwsh`)
   - git
   - Visual Studio Build Tools, with MSBuild and the v143 toolset

   If one is missing, the script says which and stops before it changes anything. You can set up by hand instead with [From Source](README.md#from-source) in the README, which uses the older ffms2 5.0 release.

   The first run compiles FFmpeg for ffms2. On one machine with 32 logical CPUs this took about 17 minutes and used about 2.7 GB under `tools/ffms2-build/`; expect yours to differ. Later runs reuse that build.

   Run it again after you pull a change to `build.py` or `build-ffms2.ps1`. A new pin in `build.py` reinstalls VapourSynth, its plugins and the encoders, and removes anything you added to `vapoursynth-portable/` by hand. A change to `build-ffms2.ps1` rebuilds ffms2. If nothing changed, it does nothing.

## Development Workflow

### Running Tests

```bash
pytest                    # Run all tests
pytest tests/test_file.py # Run a specific test file
pytest -v -x              # Verbose output, stop on first failure
```

### Type Checking

The project uses basedpyright for type checking:

```bash
basedpyright src tests
```

### Linting and Formatting

The project uses ruff for linting and formatting:

```bash
ruff check .    # Check for linting issues
ruff check . --fix  # Auto-fix linting issues
ruff format .   # Format code
```

### Running the Application

```bash
# Via entry point
videotuner

# Direct execution
python main.py "<input>.mkv"
```

## Code Conventions

- Use `from __future__ import annotations` in all modules
- Full type annotations for function signatures and class attributes
- Dataclasses for structured data
- Protocol types for duck typing
- Google-style docstrings
- Callback-based progress via `LineHandler` type for subprocess output parsing

## Submitting Changes

### Reporting Issues

Before creating an issue:

1. Search existing issues to avoid duplicates
2. Use the appropriate issue template if available
3. Provide clear reproduction steps for bugs
4. Include relevant system information (OS, Python version, FFmpeg version)

### Pull Requests

1. **Fork and branch:** Create a feature branch from `main`

   ```bash
   git checkout -b feat/your-feature-name
   ```

   Branch names must follow the convention: `<type>/<description>`

   Valid types: `feat/`, `fix/`, `docs/`, `style/`, `refactor/`, `test/`, `chore/`

2. **Make your changes:**
   - Follow the existing code style and conventions
   - Add tests for new functionality
   - Update documentation if needed

3. **Verify your changes:**

   ```bash
   pytest                    # All tests pass
   basedpyright src tests    # No type errors
   ruff check .              # No linting issues
   ruff format .             # Code is formatted
   ```

4. **Commit your changes:**
   - Write clear, descriptive commit messages
   - Reference related issues in commits when applicable

5. **Submit a pull request:**
   - Provide a clear description of the changes
   - Link to any related issues
   - Be responsive to review feedback

## Architecture Overview

The codebase follows a pipeline architecture in `src/videotuner/`:

| Module | Purpose |
| ------ | ------- |
| `pipeline.py` | Main orchestration and CLI entry point |
| `pipeline_*.py` | Pipeline modules (CLI, iteration, validation, etc.) |
| `crf_search.py` | Interpolated binary search for optimal CRF |
| `profiles.py` | YAML-based profile loading and validation |
| `encoder_type.py` | `EncoderType` enum for x264/x265 dispatch |
| `x265_params.py` | x265 parameter building with auto-detection |
| `x264_params.py` | x264 parameter building (no HDR metadata, 10-bit max) |
| `media.py` | Video metadata extraction |
| `create_encodes.py` | VapourSynth script generation and encoding |
| `vmaf_assessment.py` | VMAF quality assessment |
| `ssimulacra2_assessment.py` | SSIMULACRA2 quality assessment |
| `progress.py` | Rich console progress display |
| `constants.py` | Centralized constants (CRF limits, thread counts, metric priority, etc.) |
| `utils.py` | Shared utilities (subprocess execution, file operations) |
| `encoding_utils.py` | Encoding utilities (HDR detection, path dataclasses) |

## Questions?

If you have questions about contributing, feel free to open a discussion or issue on GitHub.
