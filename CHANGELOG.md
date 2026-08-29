# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Batch processing: pass a folder instead of a file to run the pipeline once per video inside it, with the same settings. Discovery is top level only, matched by extension (`.mkv`, `.mp4`, `.m4v`, `.mov`, `.ts`, `.m2ts`, `.avi`, `.webm`), in name order
- Settings are validated once before the first job of a batch, so a bad flag stops the batch rather than failing identically on every file
- Every file in a batch is probed up front and any expected to fail are reported before the first job starts, without aborting the rest
- Batch summary table with one row per file, carrying a column for each quality target that was set
- `batch.log` at the batch folder root, holding batch-level events and the summary
- Terminal output is now written to the job log, so a log reads the way the run looked
- `CONTEXT.md`, a glossary of the project's domain vocabulary

### Changed

- Job folders group under a batch folder in batch mode. A job folder is identical inside whether it came from a single run or a batch
- The job log is named after the input rather than the input plus a timestamp, which the job folder already carries
- `--workdir` names the batch folder in batch mode, and the job folder otherwise
- A job's log is now a single narrative. Log statements that restated console output in different words are gone; section banners and warnings keep a timestamp prefix, transcript lines do not
- A batch prints the title banner once and a `[N/M]` header per job
- Jobs in a batch run sequentially, and the run exits non-zero if any job failed

### Removed

- The second positional argument. It was documented as an output directory, was read by nothing, and was silently ignored. Use `--workdir`

### Fixed

- VMAF scores came back as NaN when the assessment output path exceeded 260 characters. libvmaf writes its JSON log with a plain `fopen`, which is capped at MAX_PATH on Windows even with long paths enabled, and it treats the failure as non-fatal, so ffmpeg exited successfully having written nothing. It is now handed a short path and the result is moved into place
- The per-job log file handler was attached to the root logger and never removed, so in a batch every later job would also write into every earlier job's log
- `logging.basicConfig(force=True)` closed existing handlers, tearing down the batch log as soon as the first job started
- A missing input called `parser.error`, raising `SystemExit` and ending the whole run instead of failing one job
- An exception raised by the job body was not caught, so one failure ended the run

## [0.4.1] - 2026-06-28

Maintenance release: dependency and build-toolchain updates, no functional changes.

### Changed

- Update the build toolchain to Nuitka 4.x
- Refresh bundled and pinned dependencies (including rich 15.x)
- Migrate `EncoderType` and `VideoFormat` to `enum.StrEnum` (no behaviour change)

## [0.4.0] - 2026-02-18

### Added

- x264 encoder support with a full encoding pipeline alongside existing x265
- `EncoderType` enum and `encoder:` key in YAML profiles to select the encoder per profile

### Changed

- Profile YAML files now require an `encoder:` key (`x264` or `x265`) on each profile
- `--preset` now requires `--encoder` to specify which encoder to use
- Default profile filename changed from `x265_profiles.yaml` to `profiles.yaml`
- Overhaul multi-profile ranking to properly handle ABR/bitrate profiles alongside CRF-based profiles
- Rename the `encoder_params` module to `x265_params` for clarity
- Generalize HEVC-specific encoding functions to support both x264 and x265 codecs

### Fixed

- Handle `CRFFloorError` in the single-profile search path
- Update vszip API to R7+ function and property names
- Always use x265 for lossless reference encoding regardless of profile encoder

## [0.3.0] - 2026-02-09

### Added

- Unified tonemapping module with automatic GPU acceleration (Vulkan/libplacebo) and CPU fallback (zscale/hable)
- HDR tonemapping support for crop detection, giving consistent results across SDR and HDR sources
- `cropdetect` CLI argument group with full parameter control: `--cropdetect-interval`, `--cropdetect-mode`, `--cropdetect-limit`, `--cropdetect-round`, `--cropdetect-mv-threshold`, `--cropdetect-low`, `--cropdetect-high`
- Two cropdetect modes: `black` (pixel threshold, default) and `mvedges` (motion vector and edge detection)
- Unified tonemapping integrated into VMAF assessment with automatic GPU/CPU branching

### Changed

- Rename `--no-autocrop` to `--no-cropdetect`
- Replace VapourSynth-based autocrop with FFmpeg `cropdetect` for more robust, dependency-free crop detection
- Default crop detection sampling interval changed to 30 seconds for denser frame sampling

### Removed

- VapourSynth `autocrop` plugin dependency; crop detection now uses FFmpeg's native `cropdetect` filter

### Fixed

- Resolve CodeQL code quality alerts: replace self-comparison NaN checks with `math.isnan()`, narrow broad exception clauses, remove dead variable assignments, replace empty except blocks with `contextlib.suppress()` or appropriate logging

## [0.2.4] - 2026-02-05

### Changed

- Add explicit `rich._unicode_data` package inclusion for Nuitka builds

### Fixed

- Fix Unicode 17.0.0 compatibility by updating Rich to >=14.3.2

## [0.2.3] - 2026-02-05

### Fixed

- Prevent duplicate CRF testing when interpolation rounds to an already-tested value
- Add exact match early termination when all targets are met and a score exactly equals its target

## [0.2.2] - 2026-01-02

### Added

- `--metric-decimals` CLI argument to control metric display and comparison precision (default: 2)

### Changed

- Update the bundled x265 encoder to 4.1+212+35 (Patman's Mod)

### Fixed

- Correct metric rounding to match display precision, fixing false negatives where visually-passing values failed programmatically

## [0.2.1] - 2025-12-22

### Added

- Optimize encoding by sharing samples when VMAF and SSIM2 use identical sampling parameters (default behaviour)

## [0.2.0] - 2025-12-22

### Added

- Migrate SSIMULACRA2 to the vszip VapourSynth plugin for improved performance and integration

### Changed

- Align SSIM2 sampling defaults with VMAF parameters for consistent sample density
- Externalize bundled dependencies to auto-download at build time with SHA256 verification

### Removed

- `--ssim2-bin` CLI argument; ssimulacra2_rs is no longer supported

## [0.1.0] - 2025-12-19

Initial release.

### Added

- CRF optimization using VMAF and SSIMULACRA2 quality metrics
- Interpolated binary search algorithm for finding optimal CRF values
- YAML-based encoding profiles with HDR/SDR conditional parameters
- Automated sample extraction and quality assessment
- Multi-profile comparison mode
- Rich console progress display

[Unreleased]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/sleepy-af-dev/VideoTuner/releases/tag/v0.4.1
