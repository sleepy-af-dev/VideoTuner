"""Constants used throughout VideoTuner."""

from __future__ import annotations

# =============================================================================
# CRF Search Constants
# =============================================================================

# x264/x265 CRF range (0 = lossless, 51 = worst quality)
CRF_MIN: float = 0.0
CRF_MAX: float = 51.0

# Maximum iterations for CRF search (safety limit to prevent infinite loops)
CRF_SEARCH_MAX_ITERATIONS: int = 20

# Extra encodes one profile may spend closing in on the bitrate budget. Small
# because these run after a search that already used its own allowance, and
# interpolation usually brackets the boundary in two or three.
BUDGET_SEARCH_MAX_ITERATIONS: int = 6

# How far up the CRF scale to step when there is no usable slope to extrapolate
# from, such as the first encode past the budget.
BUDGET_SEARCH_BLIND_STEP: float = 2.0

# CRF value used when quality floor is reached
CRF_FLOOR_VALUE: float = 51.0

# Tolerance for CRF floor comparison (floating point comparison)
CRF_FLOOR_TOLERANCE: float = 0.01

# =============================================================================
# Guard Band Constants
# =============================================================================

# Maximum combined guard percentage (start + end must be less than this)
MAX_COMBINED_GUARD_PERCENT: float = 0.99

# =============================================================================
# Bitrate Warning Constants
# =============================================================================

# Valid range for bitrate warning percentage
BITRATE_WARNING_PERCENT_MIN: float = 1.0
BITRATE_WARNING_PERCENT_MAX: float = 100.0

# =============================================================================
# VMAF Assessment Constants
# =============================================================================

# Thread calculation: use this fraction of CPU cores
VMAF_THREAD_CPU_FRACTION: float = 0.5

# Maximum threads for VMAF calculation
VMAF_MAX_THREADS: int = 16

# Concurrent frame requests for the pass that scores VMAF and SSIMULACRA2
# together. Measured on 4K PQ and HLG input samples at 32 logical CPUs:
# uncapped, the pass peaked about 9 GiB above the two separate passes; 16
# removed about 6.5 GiB of that for under 1 s per assessment. At 16 logical
# CPUs or fewer the cap equals vspipe's default and changes nothing.
COMBINED_PASS_MAX_REQUESTS: int = 16

# Frame rate at or above which the high frame rate model variant is chosen.
# 47 separates 47.952/48/50/59.94/60 from 24/25/30 without landing on any of them.
VMAF_HFR_FPS_THRESHOLD: float = 47.0

# =============================================================================
# SSIMULACRA2 Assessment Constants
# =============================================================================

# Peak luminance of the SDR display the conversion is anchored to, in cd/m2.
# It is zimg's and vs-tonemap's `nominal_luminance`: the absolute luminance a
# code value of 1.0 stands for on the SDR side, and 100 cd/m2 is the reference
# SDR display. It is not the tone curve's destination peak, which BT.2390 takes
# separately as `dst_max` and defaults to 203 cd/m2, BT.2408 reference white.
SSIM2_NOMINAL_LUMINANCE: int = 100

# Mastering display peak assumed for a PQ input that declares none, in cd/m2.
# BT.2390's tone curve needs the peak the content was graded for, and a PQ
# input carrying no mastering display gives it none, so vs-tonemap raises
# rather than guessing. 1000 cd/m2 is the reference display both BT.2100 and
# BT.2408 are written around, and the value vs-tonemap itself defaults its HLG
# peak to. BT.2408 also names 10000, the PQ container ceiling, but that is what
# the signal can hold rather than what any display is graded for.
SSIM2_ASSUMED_MASTERING_PEAK: int = 1000

# =============================================================================
# Resolution Constants
# =============================================================================

# Resolution classification tolerance (3% relative)
RESOLUTION_RELATIVE_TOLERANCE: float = 0.03

# Minimum accepted crop fraction (55% of expected dimension)
MIN_CROP_FRACTION: float = 0.55

# Confidence threshold for resolution classification
RESOLUTION_CONFIDENCE_THRESHOLD: float = 0.5

# =============================================================================
# Display Constants
# =============================================================================

# Decimal places for quality metrics (VMAF, SSIMULACRA2, etc.)
# Used for both display formatting and target comparison rounding
METRIC_DECIMALS: int = 2

# Progress bar width in characters
PROGRESS_BAR_WIDTH: int = 40

# Log section separator width
LOG_SEPARATOR_WIDTH: int = 60

# Log section separator character
LOG_SEPARATOR_CHAR: str = "="

# =============================================================================
# Frame Rate Constants
# =============================================================================

# Denominator used for FPS calculation (fps * 1000 / 1000)
FPS_DENOMINATOR: int = 1000

# =============================================================================
# Percentile Constants
# =============================================================================

# 1st percentile (bottom 1%)
PERCENTILE_1PCT: float = 0.01

# 5th percentile (bottom 5%)
PERCENTILE_5PCT: float = 0.05

# 95th percentile (top 5%)
PERCENTILE_95PCT: float = 0.95

# =============================================================================
# Metric Priority Constants
# =============================================================================

# Default priority ordering for quality metrics (highest priority first).
# Used for ranking profiles by quality scores when bitrate comparison
# is not applicable (e.g., all-ABR groups), and as a tiebreaker in mixed groups.
# When user specifies targets, targeted metrics are promoted to the front
# of this list while preserving relative order within each group.
METRIC_PRIORITY: tuple[str, ...] = (
    "vmaf_mean",
    "vmaf_hmean",
    "vmaf_1pct",
    "vmaf_min",
    "ssim2_mean",
    "ssim2_5pct",
    "ssim2_95pct",
    "ssim2_median",
)


# =============================================================================
# Path length budget
# =============================================================================

# Windows MAX_PATH is 260 including the terminating null, so 259 is the longest
# usable path. Treated as a budget rather than trusting each tool: not every
# bundled binary is long-path aware, and libvmaf in particular writes its log
# with a plain fopen that fails silently past the limit.
MAX_USABLE_PATH: int = 259

# Headroom reserved for the filename inside a job directory. The longest name
# the pipeline writes today is 42 characters.
PATH_FILENAME_MARGIN: int = 60

# Directories a job creates for its own use. Profile directories sit beside
# them, so whichever name is longer bounds how deep a job folder can be, and
# a profile named after one of these is renamed to keep them apart.
RESERVED_JOB_SUBDIRS: frozenset[str] = frozenset({"temp"})

# A job folder shortened below this stops being recognisable, so the batch
# folder gives up characters instead.
JOB_FOLDER_MIN_CHARS: int = 40

# =============================================================================
# CropDetect
# =============================================================================

# Fewest frames crop detection will judge a source on. The sample step is
# lowered to reach this on a short source, where a 30-second interval would
# otherwise yield one or two frames. One frame is not a measurement: a single
# dark or bright picture would set the crop for every encode that follows.
CROPDETECT_MIN_SAMPLES: int = 5
