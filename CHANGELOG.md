# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-09-27

### Changed

- **Breaking:** VMAF scores with the v1 models. v1 rebuilds the feature set: VIF is gone, banding (CAMBI) and chroma are scored rather than treated as side metrics, and NEG behaviour is on by default. v1 is calibrated to the v0 scale, but the numbers still move, and more on harder encodes. Measured against 0.6.0 on identical SDR encodes, where nothing else changed, the VMAF mean fell by a median of 0.6 at CRF 18 and 3.3 at CRF 26, and the 1% low by 3.1 and 8.4. **Re-derive any saved `--vmaf-*` target.** HDR moved further in the measurement; its tonemapping changed too, see the VMAF tonemapping entry below. Netflix shipped these in libvmaf 3.2.0
- The model is chosen by display and viewing distance rather than by source resolution, which is what the model names describe. A source at 2160p or above scores against `vmaf_v1.0.16_1d5h_2160`, anything below against `vmaf_v1.0.16_3d0h`, the 1080p living-room model Netflix treat as standard. A source at 47fps or above takes the matching `_hfr` variant
- VMAF is measured at 10-bit whatever the encode depth, which is what lets CAMBI see banding. When the encode was 8-bit, `cambi.enc_bitdepth` tells CAMBI so, since it scales its detection by the depth the content was encoded at rather than the depth it is handed
- **Breaking:** SSIMULACRA2 scores the image the metric was designed for. Frames are converted to linear light from the transfer the input declares, and HDR is tonemapped to SDR first with BT.2390 and gamut-mapped with BT.2407, rather than being handed to the metric as raw HDR. The previous behaviour linearised everything through the sRGB curve regardless of what the input was, so an HDR input was scored in a domain the metric was never fitted for. **Every SSIMULACRA2 score changes.** Measured against 0.6.0 on identical encodes, as the median across the test samples: SDR barely moves, its median score +0.1 at CRF 18 and +0.7 at CRF 26 and its 5% low -0.9 and -1.8, because SDR is now linearised with the BT.1886 display curve rather than sRGB. HLG's median score falls by 2.2 and 4.7. PQ varies most, in either direction depending on the content: its median score shifts from -15.6 to +14.1 across the samples, -3.3 and -4.3 at the median. Any saved `--ssim2-mean-target`, `--ssim2-median-target`, `--ssim2-95pct-target` or `--ssim2-5pct-target` needs re-deriving
- HDR runs take longer per CRF search iteration than in 0.6.0, and more so on fewer cores; SDR runs are a little faster. Median wall clock per iteration, same targets, on 32 logical CPUs: SDR 7.7% faster, PQ 7.9% slower, HLG 22% slower. Held to 4 physical cores, one sample each: SDR 1.1% faster, PQ 15.9% slower, HLG 38.5% slower. Two things add the HDR time: tonemapping on the CPU, which 0.6.0 did on the GPU for VMAF when one was available and not at all for SSIMULACRA2; and encoding at the lower CRFs that most HDR searches now settle on with the same targets. Assessment keeps several decoded frames in flight, each a full-resolution float image, so peak memory is higher too: 16.3 GiB against 0.6.0's 8.7 GiB for a 4K PQ input sample on 32 logical CPUs, measured as the peak committed memory of VideoTuner and every process it started. SSIMULACRA2 holds at most 8 frames in flight; the VMAF pipe sets no such cap, so its share can grow with core count
- HLG inputs are tonemapped against an assumed 1000 cd/m2 peak, the BT.2100 reference display. HLG carries no mastering display metadata to signal a real one, so an HLG score rests on that assumption in a way a PQ score with a declared mastering display does not
- A PQ input that declares no mastering display is tonemapped against an assumed 1000 cd/m2 peak, the BT.2100 reference display. The BT.2390 tone curve needs a peak luminance, and that metadata is normally where it comes from. The terminal and the run log both record the assumption. Scores for such an input stay comparable between encodes of it, but not against an input that declares its own peak: measured on a 4K PQ input sample, assuming 1000 where the master really was 1000 changed nothing, while assuming it against a 4000 cd/m2 master moved the mean by 0.6 points
- SSIMULACRA2 reads through the same indexer as the encoding path (`--indexer`, default `ffms2`) rather than always LSMASHSource. The source index written beside each input is named for the selected indexer's suffix, `.ffindex` or `.lwi`, so switching indexers leaves the index of the other kind on disk unused rather than replacing it, and switching back finds it still valid
- Warnings that change what a run measures now print to the terminal as well as the job log. An assumed colour tag, an assumed mastering display peak, a VMAF model fallback, a metric disabled for want of usable frames: each of these alters the numbers or drops one of them, and each was previously written to the job log alone. A run that quietly measured something other than what was asked for looked identical to one that did not. Search outcomes and cleanup failures are unchanged, since the results tables already carry the first and nothing can be done about the second
- VMAF tonemaps an HDR input with the same BT.2390 tone curve and BT.2407 gamut mapping SSIMULACRA2 uses, read through the same VapourSynth pipe, rather than with FFmpeg's own tonemapping (libplacebo where a usable GPU exists, zscale otherwise). HDR VMAF scores change as a result, and no longer depend on whether the machine has a usable GPU. Together with the v1 models, measured on identical encodes at CRF 26 against 0.6.0 on a machine with a GPU, where 0.6.0 tonemapped with libplacebo, the HDR VMAF mean fell by a median of 6.7 on PQ and 3.5 on HLG, and the 1% low by 10.6 and 5.7. Scored with the v0 models on both sides (`--v0-vmaf`), the tonemapping change alone moved the mean by less than 0.4 on every sample but one animation, where it rose by 1.4 to 2.2. How the two changes split under v1 was not measured. SDR scores are unchanged, to nine decimal places against FFmpeg reading the same two files: an SDR input needs no tonemapping, so it reaches libvmaf exactly as it comes off disk. `--indexer` now reaches VMAF as well as sample extraction and SSIMULACRA2, so switching indexers changes what VMAF reads from disk; ffms2 and lsmash decode identically to each other, so it changes no score. Its cost in speed is part of the assessment-speed entry above

- A distorted encode is indexed once per iteration rather than once per metric. In shared-sampling mode VMAF and SSIMULACRA2 score the same file, and each built its own index of it and deleted that index afterwards, so a ten-iteration search paid for twenty indexes of ten files. The index is now named beside the encode it describes, which both metrics derive identically and neither removes
- Crop detection now reads through the indexer you select with `--indexer`,
  rather than always seeking by timestamp through FFmpeg. It picks its sample
  frames by number, so it analyses the frames it asked for instead of whichever
  ones a timestamp seek landed on.
- Crop detection tonemaps an HDR input with the same operator VMAF and
  SSIMULACRA2 use. It previously used libplacebo on a machine with a Vulkan GPU
  and zscale with hable on one without, so two machines could report different
  crops from the same source. **Crop values can change, which changes every
  encode that follows.**
- Crop detection never judges a source on fewer than five frames. At the default
  30-second interval a short source could previously be cropped on the strength
  of a single frame.
- With `--as-one-source`, crops measured on the separate files are reconciled by
  taking the **smallest** per edge rather than the largest. Joining still
  requires matching dimensions, so one crop serves every file, but a file whose
  frames reach the edge now keeps the others uncropped instead of being
  overruled. That file is reporting content, not a bad measurement. The previous
  rule cut picture on a source whose active area changes part way through: a
  film with taller sequences has bars in one file and none in another, and
  taking the larger crop removed that footage. This also makes the two steps one
  rule — the smallest crop across every analysed frame of every file — which is
  the direction frames have always combined within a single file, and what the
  documentation already described. A folder whose files genuinely share their
  letterboxing is unaffected. A folder where one file has a burned-in caption or
  a logo inside the bar will now crop less, or not at all.

### Added

- `--v0-vmaf`, which scores with the legacy v0 models and reverts the measurement depth to match the encode, so a run reproduces what earlier releases did and can be compared against them. Combining it with `--vmaf-model` naming a v1 model is an error rather than a silent choice between the two
- The VMAF model generation is named in the settings header, and the exact model recorded in the job log. A fallback to v0 is marked as a fallback: v0 and v1 numbers are not comparable, and an unnoticed fallback otherwise produces v0 scores that pass for v1 against a v1 target
- A startup probe for the v1 models, run once and cached. There is no way to ask FFmpeg which libvmaf it links, so the probe scores a synthetic pair and reads the exit code. It costs about 0.07s, is skipped when its answer cannot matter, and falls back to v0 with a warning to both the terminal and the log
- A named model that the linked libvmaf cannot load now fails with a message naming the model and the libvmaf version needed, rather than an ffmpeg exit status
- Bundled vs-tonemap 0.2.0, a VapourSynth tonemapping plugin implementing the ITU-R BT.2390 tone curve and BT.2407 gamut mapping. It runs inside the VapourSynth graph, so an input's mastering display metadata is read where it still exists. HDR VMAF and SSIMULACRA2 now both need it: neither metric is defined on HDR, so an HDR input is tonemapped before either one scores it, and without the plugin that step fails. A pre-built release bundles it. An existing `vapoursynth-portable/` set up from source does not have it until `tools/update_vs.py` is re-run, and a run that needs it and cannot find it now says so rather than dropping the metric with nothing on the terminal
- The colour conversion applied to each assessment is named in the log, and a tag an input doesn't declare is reported rather than silently assumed. Only the missing tags are overwritten: an input that declares a transfer but not its primaries keeps that transfer. What's assumed depends on the conversion selected: BT.709 for an SDR input, matching what every other tool in the chain assumes for an untagged one, or BT.2020 primaries and matrix for a PQ or HLG input, since BT.709 would be the wrong guess for HDR content. Both the terminal and the log name what was missing and what was assumed
- `--indexer ffms2|lsmash`, choosing the source filter that reads and indexes video. One run now indexes one way throughout: previously, sample extraction for encoding always used ffms2 and SSIMULACRA2's own scoring step always used LSMASHSource, with no way to change either. **Scores are unaffected:** the two filters decode identically, measured on PQ, HLG and SDR inputs. The flag reaches every operation that builds an index: encoding sample extraction, VMAF, SSIMULACRA2, and crop detection
- Bundled the LSMASHSource indexer executable, so `--indexer lsmash` builds its index up front with a progress display rather than silently during the first decode
- An `--indexer` other than the default is named in the settings summary. Every frame a run reads comes through the indexer, and an archived run folder otherwise gives no way to tell which one read them
- The settings summary names the tonemapping operator a run used, when an HDR input needed one. VMAF and SSIMULACRA2 now share the same operator, so the one line names it for both

### Removed

- `--tonemap`. VMAF and SSIMULACRA2 both tonemap an HDR input unconditionally now, with the same operator, so the flag had nothing left to choose between: `off` never stopped SSIMULACRA2 from tonemapping, and now it does not stop VMAF either
- The lossless reference copy that VMAF and SSIMULACRA2 scored an encode against. Both now score directly against the source, which saves one lossless encode per job. Frame identity between the two paths was proven rather than assumed: a test hashes every sampled frame read from the source against the same frame read from the old reference, across constant and variable frame rates, a container that outlives its video track, and both indexers. The overall change in speed is in the assessment-speed entry above
- The Vulkan capability probe. Nothing tonemaps through FFmpeg any more, so
  FFmpeg no longer needs to be built with libplacebo.
- `--cropdetect-mode`, `--cropdetect-mv-threshold`, `--cropdetect-low` and
  `--cropdetect-high`. All four configured FFmpeg's `mvedges` detection mode,
  which has never worked here. `mvedges` reads motion vectors from side data
  FFmpeg attaches only when a decoder is asked for it with
  `-flags2 +export_mvs`, and no version of this tool has set that. Measured on
  a letterboxed input sample, `mvedges` returned the full frame on all 49
  frames, logging "no motion vectors available" each time, while `black` mode
  found the bars. It could not be salvaged either: crop values combine by
  taking the smallest per edge, and an I-frame carries no motion vectors and so
  returns the full frame, which drives that minimum to zero. Frames also reach
  the filter as raw y4m now, with no decoder, so the side data can never be
  present. `black` mode is unaffected and was always the default, and
  `--cropdetect-limit` and `--cropdetect-round` still tune it.
- `--vmaf-log` and `--ssim2-log`. Neither was ever read: the per-frame VMAF and
  SSIMULACRA2 JSON is written into the run folder whatever they were given, so
  both options did nothing. A script that still passes either now stops with an
  error naming it.

### Fixed

- A source whose container reports no frame count is counted from the index the run already builds, rather than estimated by multiplying its frame rate by its duration. No single rate describes a variable frame rate file, so that estimate could overshoot, and the sampler then planned samples past the end of the source. The trim clamped rather than failing, so a run quietly measured fewer frames than it was asked for. Measured on a variable frame rate input: 65 frames estimated against 48 real. When the two disagree the run now says so and names both numbers. A source that reports its own frame count is unaffected and costs nothing extra
- A relative `--workdir` no longer loses SSIMULACRA2. Its VapourSynth script was opened from the wrong folder and failed, and the run carried on with VMAF alone, noting the failure only in the job log

## [0.6.0] - 2026-09-11

Colour metadata was read from one source and compared against the vocabulary of
another, so HDR sources were never tonemapped before assessment. Scores for HDR
inputs are not comparable with those from earlier releases.

### Fixed

- HDR sources are tonemapped before VMAF scoring. The check that decides compared the colour primaries against `BT.2020`, which is how pymediainfo spells it, but that field is filled from ffprobe, which writes `bt2020`. It never matched, so `--tonemap auto`, the default, scored HDR against raw PQ values, while cropdetect, which asks the question separately, tonemapped correctly throughout. **HDR VMAF scores from this release are not comparable with those from earlier ones.** SDR is unaffected
- The tonemapping decision keys on the transfer characteristic rather than the primaries. Tone mapping compresses dynamic range, which is a property of the transfer; a BT.2020 source carrying an SDR transfer is wide gamut and wants a gamut conversion instead
- `get_video_format` classified every source as SDR, comparing the transfer against pymediainfo's spellings in the same way. Nothing in the pipeline called it, so no release behaved differently, but the encoding path had three copies of the test it should have been. It now delegates to the same HDR check as the rest of the tool, and those three copies call it
- HLG sources no longer get `--hdr10` and `--hdr10-opt`. HLG is HDR but not HDR10: x265 documents `--hdr10` as controlling the HDR10 SEI packet, which carries the mastering display and MaxCLL that an HLG source does not have, and `--hdr10-opt` as a block-level optimisation for HDR10 content. `--repeat-headers` still applies to any HDR source
- Mastering display metadata is preserved for displays outside the three named colour spaces. pymediainfo names the primaries only when they match a set it knows and prints the coordinates otherwise, and the coordinate form was discarded with a warning. It is now converted, so a title mastered on a non-standard display keeps its `--master-display`
- Bit depth is read correctly from semi-planar and packed pixel formats. `p210le` and `p410le` name the chroma layout before the depth, so reading all three digits gave 210 and 410, neither a valid depth, and both fell back to 8-bit; packed RGB such as `rgb48le` has no plane marker to read at all. Sources here are almost always planar, so this was latent rather than live
- An estimated frame count now uses the video track's duration rather than the container's. A container spans every track it holds, so its duration can run past the end of the video, and the estimate then asks the sampler for frames that do not exist. The estimate is only reached when no track reported a count of its own
- An SD source that tagged its colour primaries but not its matrix now gets a `--colormatrix`. The inference looked for `bt601`, which ffprobe never writes, since it names SD primaries `smpte170m` and `bt470bg`

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

[Unreleased]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/sleepy-af-dev/VideoTuner/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/sleepy-af-dev/VideoTuner/releases/tag/v0.4.1
