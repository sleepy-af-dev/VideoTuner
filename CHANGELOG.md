# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-08-31

Batch processing, a bitrate budget the search can aim at, and a refresh of
every bundled dependency.

### Added

- Batch processing: pass a folder instead of a file to run the pipeline once per video inside it, with the same settings. Discovery is top level only, matched by extension (`.mkv`, `.mp4`, `.m4v`, `.mov`, `.ts`, `.m2ts`, `.avi`, `.webm`), in name order
- Settings are validated once before the first job of a batch, so a bad flag stops the batch rather than failing identically on every file
- Every file in a batch is probed up front and any expected to fail are reported before the first job starts, without aborting the rest
- Batch summary table with one row per file, carrying a column for each quality target that was set
- `batch.log` at the batch folder root, holding batch-level events and the summary
- Terminal output is now written to the job log, so a log reads the way the run looked
- Job and batch folder names are shortened when the output location would otherwise push a file past the Windows 260-character path limit. A shortened name keeps a readable prefix and gains a short hash of the original, and the shortening is reported on the terminal
- The full source path is recorded in the job log, so a shortened folder name never loses the link back to its input
- A warning when a directory is created that is already too deep for the files that go in it, naming the path rather than letting a tool fail silently later
- `--carry-crf`, which starts each job in a batch at the CRF the previous job settled on rather than `--crf-start-value`. Off by default. The first job uses `--crf-start-value`, a job that fails or does not converge leaves the carried value untouched, and the carried value sets only where the search begins
- `--as-one-source`, which reads every video in the input folder as one source and produces a single result for the folder rather than one per file. Each file is sampled on its own and the samples joined, so every file keeps its own guard bands and every file contributes. Files must agree on resolution, frame rate and HDR status or the run stops, listing every mismatch; differing letterboxing is reconciled by taking the most aggressive crop across all of them. Given a single file rather than a folder it errors rather than warning
- `--show-best-within-budget`, which follows the predicted bitrate warning with the best encode that did fit the budget: its profile and CRF, its predicted bitrate, and its metrics tabled against the targets that were set. Candidates are every encode the job actually ran, not just each profile's optimum, because a CRF search walks through cheaper rate factors on its way to the answer and those are real measurements that merely missed a target. A profile that never converged still contributes what it measured. One meeting every target is offered ahead of one that does not; otherwise the highest scoring on the targeted metrics wins, and bitrate never breaks the tie because every candidate already fits. Off by default, and requires `--predicted-bitrate-warning-percent`, which is what defines the budget
- `--continue-budget-search`, which runs further encodes to find the lowest CRF that still fits rather than settling for whichever values the quality search happened to try. Bitrate falls roughly geometrically as CRF rises, so bracketing encodes are interpolated on its logarithm, usually resolving the boundary in two or three encodes. It searches in whichever direction the measurements point: upward when nothing fits yet, downward when everything does and the best quality within the budget therefore lies below what has been tried. Every CRF profile is searched, each capped at 6 extra encodes, stopping once the bracket closes to `--crf-interval` or a CRF limit is reached. Off by default, and implies `--show-best-within-budget`
- A job where no profile met its targets now reports what was reachable within budget before returning, which is when the question matters most. The job still fails and still exits non-zero
- `CONTEXT.md`, a glossary of the project's domain vocabulary

### Changed

- Job folders group under a batch folder in batch mode. A job folder is identical inside whether it came from a single run or a batch
- The job log is named `job.log`. The folder already names the job, and a filename derived from the job name was the only one that grew with it, outrunning the path budget's filename allowance on long names
- **Breaking:** `--workdir` is now the parent that run folders are created in, not the run folder itself. `--workdir C:\vt` gives `C:\vt\<name>_<timestamp>\` rather than writing straight into `C:\vt`. Previously an explicit `--workdir` replaced the timestamped folder entirely, so a second run into the same path overwrote the first without warning. The default is unchanged: `jobs`, giving `jobs/<name>_<timestamp>/`
- A job's log is now a single narrative. Log statements that restated console output in different words are gone; section banners and warnings keep a timestamp prefix, transcript lines do not
- A batch prints the title banner once and a `[N/M]` header per job
- Jobs in a batch run sequentially, and the run exits non-zero if any job failed
- A job folder now holds one folder per profile containing that profile's encodes and both metrics' results, replacing the separate `distorted/<Profile>/`, `vmaf/<Profile>/` and `ssimulacra2/<Profile>/` trees. The filenames already say which is which, so nothing is lost, a multi-profile comparison is one folder per candidate rather than three, and dropping a level of nesting gives job folder names 12 more characters before they need shortening. A profile named `reference` or `temp` gets a `_profile` suffix so it cannot clash with those folders
- Bitrate stats and analysis files no longer repeat the source name, which the job folder they sit in already carries. This alone took the longest path from 343 to 225 characters
- Profile names and run identifiers such as `iter1` or `crf16.0` are never shortened, so results stay attributable
- Bundled x264 updated to 0.165.3223+40, and the build switched from the gcc archive to the clang one to match x265
- Bundled x265 updated to 4.2+68+68. Still the `avx2` archive: it already carries the AVX-512 assembly kernels, which x265 uses only when passed `--asm avx512`, so the `avx512` archive would raise the CPU the release runs on without changing anything VideoTuner does
- Bundled VapourSynth updated to R79, on its own embedded Python 3.14. R74 rebuilt the portable install around a wheel, so VapourSynth's binaries now sit under `Lib/site-packages/vapoursynth/` rather than at the root of `vapoursynth-portable/`, and plugin autoloading is driven by `VAPOURSYNTH_EXTRA_PLUGIN_PATH` rather than by a directory sitting beside `portable.vs`. Plugins stay in `vs-plugins/` and `--vs-plugin-dir` is unchanged. Measured before and after on the same sources, both encoders produce identical scores and bitrates
- Bundled ffms2 is now built from source during the release rather than taken from the 5.0 archive, which is the newest binary the project has published and predates commits since. The build pins the ffms2 commit, the AviSynthPlus commit its headers come from, and the vcpkg tag that decides which FFmpeg is linked, and reads the FFmpeg version back afterwards rather than assuming the pin held. A manual setup still uses the 5.0 archive unless you build it yourself
- Bundled vszip updated to 22.1.0, making SSIMULACRA2 assessment substantially faster: measured at 2.38 to 13.16 frames per second on 4K samples, a little over 5x. Part of that is upstream optimisation, and part is that the plugin now ships one build per instruction set with a manifest telling VapourSynth which to load, so a machine gets code matched to its CPU rather than a single generic build. Scores are unchanged to within 3e-5 relative, which is vectorisation noise rather than a change in the metric, though a value sitting on a rounding boundary can print one hundredth differently
- vszip is now taken from its PyPI wheel rather than a GitHub release, because it stopped attaching binaries to releases after R13. It installs as `vs-plugins/vszip/` rather than a single DLL, since the per-CPU builds and their manifest belong together
- Bundled LSMASHSource updated to 1310.0.0.0. R79 warns that 1266 uses the deprecated API3 and that support for it is going away; 1310 loads on API4 and the warning is gone. Measured on the same sources, scores and bitrates are unchanged
- Building a release no longer unpacks archives with a bundled copy of 7-Zip, which the VapourSynth portable distribution stopped shipping. It uses the bsdtar included with Windows 10 and later, so 7-Zip is no longer redistributed

### Removed

- The second positional argument. It was documented as an output directory, was read by nothing, and was silently ignored. Use `--workdir`

### Fixed

- Redirecting or piping output no longer kills a run that sets a quality target. Console output is now UTF-8 whatever code page the shell hands over: previously the `>=` printed beside each target went to stdout unchanged, and on Windows a redirected stdout arrives as the legacy code page, so `videotuner ... > run.log` died with `UnicodeEncodeError` before the first encode while the same command was fine on screen
- VMAF scores came back as NaN when the assessment output path exceeded 260 characters. libvmaf writes its JSON log with a plain `fopen`, which is capped at MAX_PATH on Windows even with long paths enabled, and it treats the failure as non-fatal, so ffmpeg exited successfully having written nothing. It is now handed a short path and the result is moved into place
- The per-job log file handler was attached to the root logger and never removed, so in a batch every later job would also write into every earlier job's log
- `logging.basicConfig(force=True)` closed existing handlers, tearing down the batch log as soon as the first job started
- A missing input called `parser.error`, raising `SystemExit` and ending the whole run instead of failing one job
- An exception raised by the job body was not caught, so one failure ended the run
- Building a release left Nuitka's working directories behind in `dist/`. The cleanup looked for a name Nuitka has never produced, so nothing was ever removed and each build added a few hundred megabytes

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

[Unreleased]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/sleepy-af-dev/VideoTuner/releases/tag/v0.4.1
