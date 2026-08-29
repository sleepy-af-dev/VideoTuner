<div align="center">

![VideoTuner Logo](images/logo.png)

# VideoTuner

A CRF optimization and encoder benchmarking tool that uses VMAF and SSIMULACRA2\
quality metrics to find the optimal rate factor for video encoding.

</div>

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
  - [Periodic Sampling](#periodic-sampling)
  - [CRF Search Algorithm](#crf-search-algorithm)
  - [Automatic Crop Detection](#automatic-crop-detection)
  - [Predicted Bitrate](#predicted-bitrate)
- [Supported Encoders](#supported-encoders)
- [System Requirements](#system-requirements)
- [Installation](#installation)
  - [Pre-built Release (Recommended)](#pre-built-release-recommended)
  - [From Source](#from-source)
    - [1. Install Python Package](#1-install-python-package)
    - [2. Set Up VapourSynth Portable](#2-set-up-vapoursynth-portable)
    - [3. Install Required Plugins](#3-install-required-plugins)
    - [4. Install Encoders](#4-install-encoders)
- [Usage](#usage)
  - [Operating Modes](#operating-modes)
    - [1. CRF Search Mode (Default)](#1-crf-search-mode-default)
    - [2. Assessment-Only Mode](#2-assessment-only-mode)
    - [3. Multi-Profile Search Mode](#3-multi-profile-search-mode)
  - [Batch Processing](#batch-processing)
  - [Quality Targets](#quality-targets)
  - [Encoding Profiles](#encoding-profiles)
  - [Bitrate Mode Profiles](#bitrate-mode-profiles)
    - [Defining Bitrate Profiles](#defining-bitrate-profiles)
    - [Multi-Pass Encoding](#multi-pass-encoding)
    - [Mode Restrictions](#mode-restrictions)
    - [Multi-Profile Search with Bitrate Profiles](#multi-profile-search-with-bitrate-profiles)
  - [Auto-Detected Encoder Parameters](#auto-detected-encoder-parameters)
- [CLI Reference](#cli-reference)
  - [Mode Selection](#mode-selection)
  - [Encoding Options](#encoding-options)
  - [CropDetect Options](#cropdetect-options)
  - [Target Options](#target-options)
  - [Sampling Parameters](#sampling-parameters)
  - [Analysis Options](#analysis-options)
  - [Guard Bands](#guard-bands)
  - [Bitrate Warning](#bitrate-warning)
  - [Precision](#precision)
  - [Paths](#paths)
  - [Logging](#logging)
- [Output](#output)
  - [Job folder](#job-folder)
  - [Batch folder](#batch-folder)
  - [Path length](#path-length)
  - [Logs](#logs)
- [Development](#development)
  - [Building Releases](#building-releases)
  - [Development Commands](#development-commands)
- [Architecture](#architecture)
  - [Core Pipeline](#core-pipeline)
  - [Encoding \& Profiles](#encoding--profiles)
  - [Quality Assessment](#quality-assessment)
  - [Utilities](#utilities)
- [Notes](#notes)
- [Credits](#credits)

## Overview

VideoTuner simplifies the process of finding optimal encoding parameters by:

- Extracting representative samples using periodic frame sampling across the video
- Running iterative CRF search to find the optimal CRF that meets your quality targets
- Optionally comparing multiple encoding profiles to find the most efficient one (meets all targets at the lowest bitrate)

## How It Works

### Periodic Sampling

VideoTuner uses periodic sampling to efficiently assess video quality without encoding the entire file:

1. **Frame Selection**: Samples frames at regular intervals across the video
   - VMAF: Samples 20 consecutive frames every 1600 frames by default (~1 minute at 24fps)
   - SSIMULACRA2: Samples 20 consecutive frames every 1600 frames by default (~1 minute at 24fps)

2. **Automatic Crop Detection** (enabled by default): Analyzes the video to detect and remove letterboxing/pillarboxing before sampling

3. **Sample Encoding**: Creates concatenated sample files using VapourSynth's `SelectEvery` filter for efficient frame extraction
   - **Reference samples** are encoded losslessly to ensure 100% accuracy to the original source
   - **Distorted samples** are encoded with the test CRF/bitrate settings for quality comparison
   - Lossless encoding avoids frame-accuracy issues that would occur with direct cutting (GOP boundaries, keyframe restrictions)
   - **Note:** Lossless reference files can be large depending on sample count and source resolution

4. **Quality Assessment**: Runs VMAF and/or SSIMULACRA2 comparing distorted samples against lossless references

### CRF Search Algorithm

The CRF search uses interpolated binary search to efficiently find the optimal CRF:

1. Start at the specified CRF (default: 28)
2. Encode samples and assess quality
3. If targets are met, try higher CRF (smaller file)
4. If targets are not met, try lower CRF (better quality)
5. Use interpolation to estimate the next CRF based on score-to-CRF relationship
6. Converge when the optimal CRF is found within the specified interval

### Automatic Crop Detection

**Enabled by default** - automatically detects and removes letterboxing/pillarboxing (black bars) from your encodes. Disable with `--no-cropdetect` if needed.

**How it works:**

1. **Early Pipeline Stage**: Runs immediately after building the FFMS2 index, before any encoding
2. **Smart sampling**: Samples one frame every 30 seconds across the middle 80% of the video
3. **Conservative cropping**: Uses the minimum (safest) crop values across all sampled frames to avoid accidentally cropping content
4. **Consistent application**: The same crop values are applied to ALL encodes - reference clips, distorted clips, and across all phases

**Detection method:**

- Uses FFmpeg's `cropdetect` filter with per-sample timestamp seeking for fast detection
- Supports two modes: `black` (default, pixel threshold) and `mvedges` (motion + edge detection)
- HDR sources are automatically tonemapped to SDR before cropdetect for consistent behavior
- Detects letterboxing (black bars top/bottom) and pillarboxing (black bars left/right)
- Reports final dimensions in the console: `Detecting Crop Done! (3840x1608)`

**Benefits:**

- **More accurate quality metrics**: VMAF/SSIM2 assess actual content, not black bars
- **Maintains consistency**: All samples use the same crop values for fair comparison

### Predicted Bitrate

VideoTuner provides a **predicted bitrate** estimate for the winning profile by reading the bitrate of its encoded sample files.

**How it works:**

1. **Bitrate Extraction**: Reads the encoded bitrate from the distorted sample files using ffprobe
2. **Duration-Weighted Average**: If both VMAF and SSIM2 samples are used, calculates a weighted average:

   ```text
   predicted_bitrate = (vmaf_bitrate × vmaf_duration + ssim2_bitrate × ssim2_duration) / total_duration
   ```

**Important Notes:**

- Predicted bitrate is an **estimate** based on sampled content, not the full video
- Accuracy depends on how well the periodic samples represent the full video content
- The percentage of input bitrate is displayed when input bitrate metadata is available
- Use `--predicted-bitrate-warning-percent` to enable warnings if output exceeds a percentage of input bitrate

## Supported Encoders

- **x264 (H.264/AVC)**
- **x265 (HEVC)**

> **Note:** x264 does not support HDR metadata. VideoTuner will error if you attempt to encode an HDR source with x264.

## System Requirements

- **Operating System:** Windows 10 or Windows 11
- **Architecture:** x86-64 (64-bit only)

VideoTuner and all bundled dependencies are 64-bit Windows binaries. Linux and macOS are not currently supported.

## Installation

Both installation methods require these external tools on PATH:

- **FFmpeg** (with libvmaf and libplacebo) and **FFprobe**
- **MKVToolNix** `mkvmerge` (used to mux encoder output)

### Pre-built Release (Recommended)

Download the latest release from the [Releases page](https://github.com/sleepy-af-dev/VideoTuner/releases):

1. Download `VideoTuner-vX.X.X.zip`
2. Extract to your preferred location
3. Run `VideoTuner.exe` from the command line

**Bundled dependencies (no separate installation required):**

| Component                                                                    | Version        | Description                                                  |
| ---------------------------------------------------------------------------- | -------------- | ------------------------------------------------------------ |
| [x264](https://github.com/Patman86/x264-Mod-by-Patman)                       | 0.165.3223+26  | H.264 encoder in `tools/x264.exe`                            |
| [x265](https://github.com/Patman86/x265-Mod-by-Patman)                       | 4.1+223+43     | HEVC encoder in `tools/x265.exe`                             |
| [VapourSynth](https://github.com/vapoursynth/vapoursynth)                    | R73            | Portable environment in `vapoursynth-portable/`              |
| [ffms2](https://github.com/FFMS/ffms2)                                       | 5.0            | Frame-accurate video indexing (`ffms2.dll`, `ffmsindex.exe`) |
| [LSMASHSource](https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works) | 1266.0.0.0     | Video loading for SSIMULACRA2                                |
| [vszip](https://github.com/dnjulek/vapoursynth-zip)                          | R13            | SSIMULACRA2 quality metric calculation                       |

### From Source

Requires **Python 3.13+**. All dependencies must be installed manually.

#### 1. Install Python Package

**Using uv (Recommended):**

```bash
git clone https://github.com/sleepy-af-dev/VideoTuner.git
cd VideoTuner
uv sync
```

**Using pip:**

```bash
git clone https://github.com/sleepy-af-dev/VideoTuner.git
cd VideoTuner
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

Python dependencies (automatically installed): pyyaml, rich, pymediainfo

#### 2. Set Up VapourSynth Portable

Download `Install-Portable-VapourSynth-R73.ps1` from [VapourSynth R73 releases](https://github.com/vapoursynth/vapoursynth/releases/tag/R73) and run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File Install-Portable-VapourSynth-R73.ps1 -TargetFolder vapoursynth-portable
```

#### 3. Install Required Plugins

Download the following plugins and place them in `vapoursynth-portable/vs-plugins/`:

| Plugin       | Version    | Download                                                                                                             | Extract                              |
| ------------ | ---------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| ffms2        | 5.0        | [ffms2-5.0-msvc.7z](https://github.com/FFMS/ffms2/releases/tag/5.0)                                                  | `x64/ffms2.dll`, `x64/ffmsindex.exe` |
| LSMASHSource | 1266.0.0.0 | [L-SMASH-Works-r1266.0.0.0.7z](https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works/releases/tag/1266.0.0.0) | `x64/LSMASHSource.dll`               |
| vszip        | R13        | [vapoursynth-zip-r13-windows-x86_64.zip](https://github.com/dnjulek/vapoursynth-zip/releases/tag/R13)                | `vszip.dll`                          |

#### 4. Install Encoders

Download encoders and place in `tools/`:

| Component | Version        | Download                                                                                               | Extract                       |
| --------- | -------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------- |
| x264      | 0.165.3223+26  | [x264-0.165.3223+26...7z](https://github.com/Patman86/x264-Mod-by-Patman/releases/tag/0.165.3223%2B26) | `x264.exe` → `tools/x264.exe` |
| x265      | 4.1+223+43     | [x265-4.1+223+43...7z](https://github.com/Patman86/x265-Mod-by-Patman/releases/tag/4.1%2B223%2B43)     | `x265.exe` → `tools/x265.exe` |

## Usage

The tool can be run using `python main.py` or the installed console script `videotuner`.

### Operating Modes

VideoTuner has three operating modes:

#### 1. CRF Search Mode (Default)

Finds the optimal CRF value that meets your quality targets using iterative binary search.

```bash
# Find optimal CRF with default preset (slow) using x265
videotuner input.mkv --encoder x265 --preset slow --vmaf-target 95

# Find optimal CRF with a faster preset using x264
videotuner input.mkv --encoder x264 --preset medium --vmaf-target 95

# Find optimal CRF with a specific profile (encoder is defined in the profile)
videotuner input.mkv --profile Film --vmaf-target 95 --ssim2-mean-target 80
```

#### 2. Assessment-Only Mode

Performs a single encode at a specified CRF without any quality targets - useful for quick quality checks.

```bash
# Single assessment at CRF 18 using x265 medium preset
videotuner input.mkv --encoder x265 --preset medium --assessment-only --crf-start-value 18

# Assessment with a specific profile (encoder is defined in the profile)
videotuner input.mkv --assessment-only --profile Film --crf-start-value 20
```

#### 3. Multi-Profile Search Mode

Compares multiple encoding profiles by running independent CRF searches for each, then selects the profile with the lowest predicted bitrate that meets all quality targets.

```bash
# Compare profiles by name
videotuner input.mkv --multi-profile-search Film,Grain,Animation --vmaf-target 95

# Compare profiles by group
videotuner input.mkv --multi-profile-search film-group,animation-group --vmaf-target 95

# Mix profiles and groups
videotuner input.mkv --multi-profile-search Film,animation-group --vmaf-target 95
```

**How Multi-Profile Search Works:**

1. **Run CRF Search for Each Profile**: Performs a complete CRF search for each profile to find the optimal CRF value that meets all quality targets
2. **Rank Results**: Profiles that meet all targets are ranked above those that don't. Within each tier, CRF profiles are ranked by predicted bitrate (lowest wins) with quality scores as a tiebreaker; all-bitrate groups are ranked by quality scores only
3. **Select Winner**: The best-ranked profile is selected as the winner

**Performance Optimization:** Each subsequent profile's CRF search starts at the previous profile's optimal CRF value for faster convergence.

### Batch Processing

Pass a folder instead of a file and VideoTuner runs the same pipeline once per video inside it, with the same settings. Any mode above works in a batch.

```bash
# CRF search across every video in a folder
videotuner ./season1 --encoder x265 --preset slow --vmaf-target 95

# Batch with a profile
videotuner ./season1 --profile Film --vmaf-target 95

# Batch multi-profile comparison; the summary names the winner per file
videotuner ./season1 --multi-profile-search Film,Grain --vmaf-target 95
```

**Which files are picked up:** video files at the top level of the folder only, matched by extension (`.mkv`, `.mp4`, `.m4v`, `.mov`, `.ts`, `.m2ts`, `.avi`, `.webm`) and processed in name order. Subfolders are not searched.

**How a batch behaves:**

- Settings are validated once before the first job, so a bad flag stops the batch immediately instead of failing identically on every file.
- Every file is then probed and you are warned up front about any that are expected to fail, such as HDR sources when the chosen profile uses x264. The rest of the batch still runs.
- A job that fails is recorded and the batch continues to the next file.
- Jobs run one at a time. The encoders already use every core, so running two at once makes both slower.
- The batch ends with a summary table, one row per file, and exits non-zero if any job failed.

**Carrying the CRF between jobs:** `--carry-crf` starts each job at the CRF the previous job settled on, instead of `--crf-start-value`. It is off by default.

```bash
videotuner ./folder --profile Film --vmaf-target 95 --carry-crf
```

The first job uses `--crf-start-value`, since there is no previous result. A job that fails or does not converge leaves the carried value untouched, so the next job starts from the last CRF that was found. In multi-profile mode it is the winning profile's CRF that carries, whichever profile won.

The carried value only sets where the search begins; it does not constrain where the search ends, so each job still converges on its own answer.

**Note:** batch mode writes an `.ffindex` file next to each source video, the same as a single-file run. They are reused on later runs over the same folder, which is the largest available time saving when re-running a batch.

### Quality Targets

Quality targets define the minimum acceptable scores. CRF search will find the highest CRF (smallest file) that meets all targets.

```bash
# VMAF targets
--vmaf-target 95           # Target VMAF mean score
--vmaf-hmean-target 94     # Target VMAF harmonic mean score
--vmaf-1pct-target 90      # Target VMAF 1% low score
--vmaf-min-target 85       # Target VMAF minimum score

# SSIMULACRA2 targets
--ssim2-mean-target 80     # Target SSIM2 mean score
--ssim2-median-target 82   # Target SSIM2 median score
--ssim2-95pct-target 85    # Target SSIM2 95% high score
--ssim2-5pct-target 75     # Target SSIM2 5% low score
```

**Note:** At least one target is required unless using `--assessment-only` mode. Multiple targets can be combined - all must be met.

### Encoding Profiles

Define encoding profiles in `profiles.yaml`. Each profile specifies an encoder and parameters that control encoding quality vs. speed tradeoffs.

```yaml
profiles:
  - name: Film
    encoder: x265
    description: Optimized for live-action film content
    groups:
      - film-group
    settings:
      preset: slow
      aq-mode: 3
      psy-rd: 2.0
      psy-rdoq: 1.0

  - name: Animation-AVC
    encoder: x264
    description: Optimized for animated content (H.264)
    groups:
      - animation-group
    settings:
      preset: slow
      aq-mode: 1
      psy-rd: 1.0
      deblock: "-1:-1"
```

**Key features:**

- **Encoder selection**: Each profile declares its encoder (`x264` or `x265`)
- **Groups**: Organize profiles into groups for easy selection with `--multi-profile-search`
- **Conditional parameters**: Use `hdr`/`sdr` keys for format-specific settings (x265 only)
- **Auto-detection**: Color space, bit depth, and HDR metadata are automatically detected from source
- **Validation**: Invalid parameters are caught with helpful error messages

An example profile file is included: `profiles.yaml.sample`

**Preset vs Profile:**

- `--preset`: Use a built-in preset (ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow, placebo). Requires `--encoder`. Default: `slow`
- `--profile`: Use a custom profile from `profiles.yaml` (encoder is defined in the profile)

These options are mutually exclusive - use one or the other.

### Bitrate Mode Profiles

In addition to CRF-based profiles, VideoTuner supports **bitrate mode profiles** that encode at a fixed bitrate instead of using CRF quality targeting.

#### Defining Bitrate Profiles

Add a `bitrate` setting (in kbps) to your profile:

```yaml
profiles:
  - name: Streaming-4K
    encoder: x265
    description: Fixed bitrate for 4K streaming
    settings:
      preset: slow
      bitrate: 15000  # 15 Mbps
      aq-mode: 3
      psy-rd: 2.0
```

#### Multi-Pass Encoding

Bitrate profiles support multi-pass encoding for improved quality at the target bitrate. VideoTuner automatically runs all required passes internally.

```yaml
profiles:
  # 2-Pass encoding (VideoTuner runs: pass 1 → pass 2)
  - name: Streaming-2Pass
    encoder: x265
    settings:
      preset: slow
      bitrate: 8000
      pass: 2

  # 3-Pass encoding (VideoTuner runs: pass 1 → pass 3 → pass 2)
  - name: Streaming-3Pass
    encoder: x265
    settings:
      preset: slow
      bitrate: 8000
      pass: 3
      multi-pass-opt-analysis: true
      multi-pass-opt-distortion: true
```

**Pass settings:**

- `pass: 2` - 2-pass encoding (VideoTuner runs pass 1 → pass 2 automatically)
- `pass: 3` - 3-pass encoding (VideoTuner runs pass 1 → pass 3 → pass 2 automatically)

**x265 pass execution order (1 → 3 → 2):**

- Pass 1: Generates initial stats file
- Pass 3: Refines stats file
- Pass 2: Final encoding using refined stats

#### Mode Restrictions

**CRF Search Mode** (default, single profile):

- Bitrate profiles **cannot** be used in CRF search mode
- Use `--assessment-only` for a single bitrate encode, or `--multi-profile-search` for comparison

**Assessment-Only Mode:**

- Bitrate profiles work normally - encodes at the specified bitrate and reports quality scores

#### Multi-Profile Search with Bitrate Profiles

Bitrate profiles can be used in `--multi-profile-search` either alongside CRF profiles or on their own:

**Mixed CRF and Bitrate Profiles:**

```bash
videotuner input.mkv --multi-profile-search Film,Streaming-4K --vmaf-target 95
```

- CRF profiles: Run full CRF search to find optimal CRF meeting targets
- Bitrate profiles: Run single encode at specified bitrate and evaluate against targets (pass/fail only, no CRF iteration)
- Winner selection: Profiles meeting all targets are ranked above those that don't, then by lowest predicted bitrate with quality score tiebreaker
- Both CRF and bitrate profiles are treated equally within ranking tiers

**All Bitrate Profiles:**

```bash
videotuner input.mkv --multi-profile-search Streaming-4K,Streaming-1080p
```

When all profiles are bitrate mode:

- Each profile encodes at its specified bitrate
- Quality targets are optionally evaluated (pass/fail only)
- Profiles are ranked by quality scores (VMAF, then SSIMULACRA2) rather than bitrate
- A winner is declared based on the best quality scores

### Auto-Detected Encoder Parameters

The following parameters are **automatically detected from the source video** and set by default. You can override any of these in your profile if needed.

**Shared parameters (x264 and x265):**

| Parameter      | Default Behavior                                                                           |
| -------------- | ------------------------------------------------------------------------------------------ |
| `colorprim`    | Mapped from source color primaries (e.g., BT.709 → `bt709`, BT.2020 → `bt2020`)            |
| `transfer`     | Mapped from source transfer characteristics (e.g., PQ → `smpte2084`, HLG → `arib-std-b67`) |
| `colormatrix`  | Mapped from source color space, or inferred from primaries if unavailable                  |
| `range`        | Set from source color range (x265: `limited`/`full`, x264: `tv`/`pc`)                      |
| `output-depth` | Detected from source pixel format (x265: 8/10/12-bit, x264: 8/10-bit max)                  |
| `chromaloc`    | Preserved from source chroma sample location if present                                    |

**x265-only parameters:**

| Parameter        | Default Behavior                                            |
| ---------------- | ----------------------------------------------------------- |
| `hdr10`          | Enabled for HDR content (PQ/HLG transfer), disabled for SDR |
| `hdr10-opt`      | Enabled for HDR content for QP optimization                 |
| `repeat-headers` | Enabled for HDR (required), disabled for SDR                |
| `master-display` | Extracted from source HDR mastering display metadata        |
| `max-cll`        | Extracted from source MaxCLL/MaxFALL if present             |
| `aud`            | Always enabled (Access Unit Delimiters for compatibility)   |
| `hrd`            | Enabled for non-lossless encodes (VBV compliance)           |

**Example: Override auto-detected parameters:**

```yaml
profiles:
  - name: Force-SDR
    encoder: x265
    description: Force SDR output regardless of source
    settings:
      preset: slow
      hdr10: false       # Will add --no-hdr10
      repeat-headers: false  # Will add --no-repeat-headers
      output-depth: 8    # Force 8-bit output
```

## CLI Reference

Run `videotuner --help` for complete options. Key options include:

### Mode Selection

| Option                            | Description                                        |
| --------------------------------- | -------------------------------------------------- |
| `--assessment-only`               | Single assessment without quality targets          |
| `--multi-profile-search PROFILES` | Compare multiple profiles/groups (comma-separated) |

### Encoding Options

| Option                   | Default | Description                                                  |
| ------------------------ | ------- | ------------------------------------------------------------ |
| `--encoder ENCODER`      | -       | Encoder to use: `x264` or `x265` (required with `--preset`) |
| `--preset PRESET`        | `slow`  | Encoder preset (mutually exclusive with `--profile`)         |
| `--profile NAME`         | -       | Profile name from `profiles.yaml`                            |
| `--crf-start-value CRF`  | `28`   | Starting CRF for search                                      |
| `--carry-crf`            | off    | Batch mode: start each job at the CRF the previous job settled on |
| `--crf-interval STEP`    | `0.5`  | Minimum CRF step size                                        |

### CropDetect Options

| Option                      | Default      | Description                                       |
| --------------------------- | ------------ | ------------------------------------------------- |
| `--no-cropdetect`           | -            | Disable automatic crop detection                  |
| `--cropdetect-interval`     | `30`         | Seconds between sampled frames for crop detection |
| `--cropdetect-mode`         | `black`      | Detection mode: `black` or `mvedges`              |
| `--cropdetect-limit`        | FFmpeg 24    | Black pixel threshold 0-255                       |
| `--cropdetect-round`        | `2`          | Crop dimension divisibility (FFmpeg default: 16)  |
| `--cropdetect-mv-threshold` | FFmpeg 8     | Motion vector threshold in pixels                 |
| `--cropdetect-low`          | FFmpeg ~0.02 | Canny low threshold 0.0-1.0                       |
| `--cropdetect-high`         | FFmpeg ~0.06 | Canny high threshold 0.0-1.0                      |

### Target Options

**VMAF Targets:**

| Option                | Description                     |
| --------------------- | ------------------------------- |
| `--vmaf-target`       | Target VMAF mean score          |
| `--vmaf-hmean-target` | Target VMAF harmonic mean score |
| `--vmaf-1pct-target`  | Target VMAF 1% low score        |
| `--vmaf-min-target`   | Target VMAF minimum score       |

**SSIMULACRA2 Targets:**

| Option                  | Description                       |
| ----------------------- | --------------------------------- |
| `--ssim2-mean-target`   | Target SSIMULACRA2 mean score     |
| `--ssim2-median-target` | Target SSIMULACRA2 median score   |
| `--ssim2-95pct-target`  | Target SSIMULACRA2 95% high score |
| `--ssim2-5pct-target`   | Target SSIMULACRA2 5% low score   |

### Sampling Parameters

| Option                    | Default | Description                         |
| ------------------------- | ------- | ----------------------------------- |
| `--vmaf-interval-frames`  | `1600`  | Sample every N frames for VMAF      |
| `--vmaf-region-frames`    | `20`    | Consecutive frames per VMAF sample  |
| `--ssim2-interval-frames` | `1600`  | Sample every N frames for SSIM2     |
| `--ssim2-region-frames`   | `20`    | Consecutive frames per SSIM2 sample |

### Analysis Options

| Option         | Default | Description                                                |
| -------------- | ------- | ---------------------------------------------------------- |
| `--no-vmaf`    | -       | Disable VMAF assessment                                    |
| `--no-ssim2`   | -       | Disable SSIMULACRA2 assessment                             |
| `--vmaf-model` | `auto`  | VMAF model name or path (auto-selects based on resolution) |
| `--tonemap`    | `auto`  | HDR tonemapping: auto\|force\|off                          |

### Guard Bands

| Option                  | Default | Description                       |
| ----------------------- | ------- | --------------------------------- |
| `--guard-start-percent` | `0.0`   | Exclude head (start) percentage   |
| `--guard-end-percent`   | `0.0`   | Exclude tail (end) percentage     |
| `--guard-seconds`       | `0`     | Minimum guard in seconds per side |

### Bitrate Warning

| Option                                | Description                                                     |
| ------------------------------------- | --------------------------------------------------------------- |
| `--predicted-bitrate-warning-percent` | Warn if output exceeds this percentage of input bitrate (1-100) |

### Precision

| Option              | Default | Description                                                 |
| ------------------- | ------- | ----------------------------------------------------------- |
| `--metric-decimals` | `2`     | Decimal places for target comparison (display matches this) |

### Paths

| Option            | Default                   | Description                    |
| ----------------- | ------------------------- | ------------------------------ |
| `--workdir`       | `jobs`                    | Parent folder that run folders are created in |
| `--ffmpeg`        | `ffmpeg`                  | FFmpeg binary                  |
| `--ffprobe`       | `ffprobe`                 | FFprobe binary                 |
| `--mkvmerge`      | `mkvmerge`                | MKVmerge binary                |
| `--vs-dir`        | `./vapoursynth-portable`  | VapourSynth portable directory |
| `--vs-plugin-dir` | `<vs-dir>/vs-plugins`     | VapourSynth plugin directory   |

### Logging

| Option             | Description                                            |
| ------------------ | ------------------------------------------------------ |
| `-v` / `--verbose` | Add debug detail to the log                            |
| `-q` / `--quiet`   | Reduce the log to warnings only                        |
| `--log-file`       | Write the job log somewhere other than the job folder  |

Both `-v` and `-q` affect the log file only; terminal output is unchanged.

## Output

### Job folder

One job is one source video processed end to end. Its output all lands in one folder.

- Default location: `jobs/<input_name>_<timestamp>`
- `--workdir <path>` changes the parent, giving `<path>/<input_name>_<timestamp>`

`--workdir` names the folder that run folders go in, never a run folder itself. Every run always gets its own `<name>_<timestamp>` folder, so pointing several runs at the same `--workdir` is safe and they cannot overwrite each other. A short `--workdir` is the simplest way to buy headroom against the path limit below.

**Folder Structure:**

```text
jobs/<input_name>_<timestamp>/
├── reference/                                # Lossless reference samples
│   ├── vmaf_reference_concatenated.mkv
│   └── ssim2_reference_concatenated.mkv
├── <ProfileName>/                            # Everything produced for one profile
│   ├── vmaf_crf_*.mkv                        # VMAF distorted at each CRF iteration
│   ├── ssim2_crf_*.mkv                       # SSIM2 distorted at each CRF iteration
│   ├── vmaf_concatenated_iter*.json          # VMAF assessment results
│   └── ssim2_concatenated_iter*.json         # SSIMULACRA2 assessment results
├── temp/                                     # VapourSynth scripts, encoder bitstreams
└── <input_name>.log                          # Job log
```

**Notes:**

- All encoded samples and assessment results are preserved across iterations for inspection
- Each profile gets one folder holding its encodes and both metrics' results, so a multi-profile comparison is one folder per candidate rather than three
- A profile named `reference` or `temp` would clash with the folders above, so it gets a `_profile` suffix

### Batch folder

A batch groups the job folders of every video in one input folder. A job folder is identical inside whether it came from a single run or from a batch.

- Default location: `jobs/<input_folder_name>_<timestamp>`
- `--workdir <path>` changes the parent, giving `<path>/<input_folder_name>_<timestamp>`

```text
jobs/<input_folder_name>_<timestamp>/
├── batch.log                     # Batch-level events and the final summary
├── <video_1_name>/               # Job folder, structured exactly as above
├── <video_2_name>/
└── ...
```

Two inputs whose names differ only by extension (`clip.mkv` and `clip.mp4`) would collide on one job folder name, so the second gets a numeric suffix rather than overwriting the first.

### Path length

Windows caps a path at 260 characters, and not every bundled tool handles more even when long paths are enabled system-wide. VideoTuner keeps every file it writes inside that limit.

Before a job folder is created, its name is measured against the room left by the output location, the deepest subfolder and the profile name. If it does not fit, the name is shortened and a short hash of the original is appended:

```text
a-long-example-input-filename-that-does-not-fit-the-path-budget
                          becomes
a-long-example-input-filename-that-doe~cdc179
```

The full source path is always recorded in the job log, so a shortened name never loses the link back to its input. Shortening is reported on the terminal when it happens, and profile names and run identifiers such as `iter1` or `crf16.0` are never shortened. If the output location is so deep that even a shortened name will not fit, VideoTuner warns at startup rather than failing partway through; a shorter `--workdir` is the fix.

### Logs

The job log is a transcript: everything printed to the terminal during that job is written to it, so the log reads the way the run looked. Section banners and anything at warning level or above carry a timestamp; transcript lines do not, which keeps tables and panels readable.

`batch.log` holds batch-level events and the summary table only. Each job's detail stays in that job's own log, so the file you open after an overnight run is the short one.

`-v` adds debug detail to the log, `-q` reduces it to warnings only. Neither changes what appears on the terminal.

## Development

Install with dev dependencies for testing, linting, and type checking:

```bash
# Using uv
uv sync --extra dev

# Or using pip
pip install -e ".[dev]"
```

### Building Releases

To build a compiled release, install the build dependencies:

```bash
# Using uv
uv sync --extra build

# Or using pip
pip install -e ".[build]"
```

Then run the build script:

```bash
python build.py
```

This creates a release folder in `dist/VideoTuner-vX.X.X/` with the compiled executable and all required files.

**Note:** Building requires a C compiler (Visual Studio Build Tools on Windows).

### Development Commands

```bash
# Run tests
pytest

# Type checking
basedpyright src tests

# Linting and formatting
ruff check .
ruff format .
```

## Architecture

The pipeline is organized into modules:

### Core Pipeline

- `pipeline.py` - Main orchestration and workflow coordination
- `pipeline_cli.py` - CLI argument parsing, validation, and PipelineArgs dataclass
- `pipeline_iteration.py` - Core iteration functions for CRF/bitrate encoding and bitrate prediction
- `pipeline_multi_profile.py` - Multi-profile comparison and ranking logic
- `pipeline_validation.py` - Assessment validation, target checking, and sampling validation
- `pipeline_types.py` - Shared dataclasses and path management utilities
- `pipeline_display.py` - Rich console display functions for settings and results
- `pipeline_reference.py` - Lossless reference generation for quality metrics
- `crf_search.py` - Interpolated binary search algorithm for CRF optimization

### Encoding & Profiles

- `profiles.py` - Profile loading, validation, and group management from YAML config
- `encoder_type.py` - `EncoderType` enum for x264/x265 dispatch
- `x265_params.py` - x265 parameter building, validation, and auto-detection of color/HDR metadata
- `x264_params.py` - x264 parameter building (no HDR metadata, 10-bit max)
- `create_encodes.py` - VapourSynth script generation, encoder-agnostic encoding (CRF and multi-pass bitrate modes)
- `encoding_utils.py` - Shared encoding utilities (HDR detection, VapourSynthPaths/EncoderPaths dataclasses, path resolution)

### Quality Assessment

- `vmaf_assessment.py` - VMAF quality metric assessment and result parsing
- `ssimulacra2_assessment.py` - SSIMULACRA2 quality metric assessment and result parsing

### Utilities

- `media.py` - Media probing and metadata extraction via FFprobe/VapourSynth
- `utils.py` - Shared utility functions (subprocess execution, float formatting, file operations)
- `progress.py` - Rich console output, progress bars, and subprocess line handlers
- `constants.py` - Centralized constants (CRF limits, thread counts, display settings, tolerances)

## Notes

- Sample encoding uses VapourSynth → x264/x265, then mkvmerge for MKV container
- For VFR sources, duration calculations use average frame rate (approximation)
- Profile names and group names must be unique and non-overlapping
- CRF search will error if it reaches CRF 1 without meeting targets

## Credits

- Inspired by [ab-av1](https://github.com/alexheretic/ab-av1) by alexheretic
- This project was developed with the assistance of [Claude Code](https://claude.com/product/claude-code), Anthropic's AI-powered development tool
