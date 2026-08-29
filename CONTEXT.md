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
