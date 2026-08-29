# VideoTuner

CRF optimization and encoder benchmarking. Extracts representative samples from a
source video, encodes them under candidate settings, and scores the result against
quality metrics to find the rate factor that meets a target.

## Language

### Work units

**Job**:
One source video processed end to end, from sample extraction through to a final
result. The unit of work everything else is counted in.
_Avoid_: run, task, encode

**Source**:
The video a job reads. Usually one file; with `--as-one-source`, every file in
the input folder, sampled individually and joined so they are assessed as one.
_Avoid_: input (that is what the user passed on the command line, which may be
a folder)

**Batch**:
A set of jobs sharing one set of settings, produced from the videos found in a
single input folder.
_Avoid_: bulk run, queue

**Job folder**:
The folder holding everything a job produced: its samples, its assessment results,
and its log.
_Avoid_: output folder, working directory (`--workdir` is the CLI spelling, and in
batch mode it names the batch folder instead)

**Batch folder**:
The folder grouping the job folders of one batch, and holding the batch log and the
summary of how every job in it turned out.

### Assessment

**Sample**:
A short region of frames taken from the source at a fixed interval. Samples stand in
for the whole video so a job does not have to encode all of it.

**Reference**:
The losslessly extracted samples, used as the baseline a metric scores against.

**Distorted**:
The samples after encoding under the settings being assessed. Scored against the
reference.

**Profile**:
A named, reusable set of encoder settings, including which encoder it belongs to.
_Avoid_: preset (a preset is one setting within a profile, not a synonym for it)

**Quality target**:
A metric threshold a job must meet, such as a minimum mean VMAF. A job searches for
the rate factor that satisfies every target it was given.
_Avoid_: goal, threshold

**Budget**:
The largest predicted bitrate a result can have and still be worth offering, set by
`--predicted-bitrate-warning-percent` as a percentage of the source's own bitrate.
A job optimizes for its quality targets, never for the budget; exceeding it is
reported after the fact, alongside the best encode that stayed within it.
_Avoid_: bitrate target (a target is what a job searches for, which this is not),
bitrate limit (nothing is prevented from exceeding it)

**Point**:
One encode that was run and scored, at a known predicted bitrate. Every CRF
iteration produces one and so does every bitrate-mode encode, which is why a
search that missed its targets still leaves usable measurements behind. The unit
the budget search chooses between.
_Avoid_: result (that is a profile's outcome, one per profile, not one per encode)

**Budget search**:
Encoding further CRF values to find the lowest one that still fits the budget,
run only when asked for with `--continue-budget-search`. Distinct from the CRF
search, which seeks the highest rate factor meeting every quality target.
