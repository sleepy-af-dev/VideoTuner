"""Does sampling a source give the frames the lossless reference holds?

The reference is an x265 ``--lossless`` encode of the sampled frames, and it
exists because streams went out of sync in the past. Whether it can be dropped
is a question about frame selection rather than about compression, so these
tests hash every plane of every sampled frame on both sides and demand an exact
match, across a constant frame rate, a variable one, a container that outlives
its video track, and both indexers.

Both arms go through the production code: the reference is built by
``encode_concatenated_reference`` and the sampling script by
``build_sampling_vpy_script``. A second, parallel implementation of either
would compare this file against itself rather than against the pipeline.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from videotuner.create_encodes import (
    encode_concatenated_distorted,
    encode_concatenated_reference,
)
from videotuner.encoder_type import EncoderType
from videotuner.encoding_utils import (
    IndexerType,
    SampledSource,
    UsableRange,
    VapourSynthEnv,
    build_sampling_vpy_script,
    indexer_spec,
    write_vpy_script,
)
from videotuner.media import get_frame_count, parse_video_info
from videotuner.profiles import Profile

REPO_ROOT = Path(__file__).resolve().parents[1]
VS_DIR = REPO_ROOT / "vapoursynth-portable"
VS_PYTHON = VS_DIR / "python.exe"
VS_PLUGINS = VS_DIR / "vs-plugins"
X265_BIN = REPO_ROOT / "tools" / "x265.exe"

# Sampling that lands several separated regions inside an eight second clip,
# so the comparison spans the whole timeline rather than one opening burst.
INTERVAL_FRAMES = 24
REGION_FRAMES = 5

# The profile the pipeline builds for reference extraction, copied from
# pipeline.py. x265 for both codecs, because only it handles HDR.
LOSSLESS_PROFILE = Profile(
    name="lossless",
    description="Lossless reference extraction",
    settings={"preset": "ultrafast"},
    encoder=EncoderType.X265,
)

requires_vapoursynth = pytest.mark.skipif(
    not VS_PYTHON.exists() or not VS_PLUGINS.exists(),
    reason="vapoursynth-portable is not provisioned; run tools/update_vs.py",
)

requires_x265 = pytest.mark.skipif(
    not X265_BIN.exists(),
    reason="tools/x265.exe is not provisioned; run tools/update_vs.py",
)

requires_mkvmerge = pytest.mark.skipif(
    shutil.which("mkvmerge") is None,
    reason="mkvmerge is not on PATH",
)


def _ffmpeg_can_build_the_inputs() -> bool:
    """Decide once whether the ffmpeg on PATH can write the fixtures.

    Every fixture is a 10-bit HEVC encode of ffmpeg's own test pattern, so
    libx265 is the only capability beyond a stock build.

    Returns:
        True when ffmpeg is present and lists libx265 among its encoders.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return "libx265" in proc.stdout


requires_libx265 = pytest.mark.skipif(
    not _ffmpeg_can_build_the_inputs(),
    reason="ffmpeg on PATH has no libx265 encoder",
)

_PATTERN = "testsrc2=size=640x360:rate=24:duration=8"
_LOSSLESS_HEVC = [
    "-pix_fmt",
    "yuv420p10le",
    "-c:v",
    "libx265",
    "-x265-params",
    "lossless=1",
]

# Each case is the ffmpeg input and filter arguments that build it; the
# lossless HEVC output arguments are shared. "vfr" keeps one frame in three
# and respaces what is left, which leaves a stream whose instantaneous rate
# alternates between 24 and 12 fps. "longtail" mixes in twenty seconds of
# audio against eight seconds of video, so the container runs past the last
# frame and a frame count taken from its duration overshoots.
_FIXTURES: dict[str, list[str]] = {
    "cfr": ["-f", "lavfi", "-i", _PATTERN],
    "vfr": [
        "-f",
        "lavfi",
        "-i",
        _PATTERN,
        "-vf",
        "select='not(mod(n,3))',setpts=N/(24*TB)*1.37",
        "-fps_mode",
        "vfr",
    ],
    "longtail": [
        "-f",
        "lavfi",
        "-i",
        _PATTERN,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=20",
        "-c:a",
        "aac",
    ],
}


@pytest.fixture(scope="session")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build every test input once, outside the working tree.

    Args:
        tmp_path_factory: pytest's session-scoped temporary directory maker.

    Returns:
        Each case name mapped to the file built for it.
    """
    directory = tmp_path_factory.mktemp("frame_accuracy_inputs")
    built: dict[str, Path] = {}
    for name, args in _FIXTURES.items():
        path = directory / f"{name}.mkv"
        _ = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *args, *_LOSSLESS_HEVC, str(path)],
            capture_output=True,
            check=True,
        )
        built[name] = path
    return built


@dataclass(frozen=True)
class _Comparison:
    """Per-frame hashes read back from both sides of one case.

    Attributes:
        sampled: Hashes of the frames the sampling script selects.
        reference: Hashes of the frames the encoded reference holds.
        sampled_source: Absolute path the sampling script read.
        reference_source: Absolute path the reference arm read.
    """

    sampled: list[str]
    reference: list[str]
    sampled_source: str
    reference_source: str


def _sampled_source(path: Path, tmp_path: Path, indexer: IndexerType) -> SampledSource:
    """Describe a whole file as the pipeline's sampler would see it.

    The usable range comes from the same metadata the pipeline reads, so a
    container whose duration overshoots its video track overshoots here too.

    Args:
        path: The input file.
        tmp_path: Directory to keep the index in.
        indexer: Source filter that will read the file.

    Returns:
        The file with its index and usable range.
    """
    info = parse_video_info(path, log_hdr_metadata=False)
    frames = get_frame_count(path, info)
    suffix = indexer_spec(indexer).index_suffix
    return SampledSource(
        path=path,
        cache_file=tmp_path / f"{path.stem}{suffix}",
        usable_range=UsableRange(start=0, end=frames, frame_count=frames),
    )


_HASH_EPILOGUE = """

import hashlib


def _hashes(clip):
    out = []
    for frame in clip.frames(close=True):
        digest = hashlib.sha256()
        for plane in range(frame.format.num_planes):
            digest.update(bytes(frame[plane]))
        out.append(digest.hexdigest())
    return out


reference = {load}
print("SAMPLEDSOURCE:{sampled_source}")
print("REFERENCESOURCE:{reference_source}")
for value in _hashes(sampled):
    print("SAMPLED:" + value)
for value in _hashes(reference):
    print("REFERENCE:" + value)
"""


def _compare(
    source: SampledSource,
    reference: Path,
    fps: float,
    tmp_path: Path,
    indexer: IndexerType,
) -> _Comparison:
    """Hash the sampling script's frames and the encoded clip's frames.

    The sampling half is the script ``build_sampling_vpy_script`` emits, with
    a hashing epilogue appended; the clip it selects from is never re-derived.

    Args:
        source: The file to sample, with its index and usable range.
        reference: The encoded clip to compare against.
        fps: Frame rate the sampler renumbers to.
        tmp_path: Directory for the script and the reference's own index.
        indexer: Source filter both sides load through.

    Returns:
        Both hash lists and the path each side read.

    Raises:
        AssertionError: If the script fails under VapourSynth.
    """
    sampling = build_sampling_vpy_script(
        sources=[source],
        interval_frames=INTERVAL_FRAMES,
        region_frames=REGION_FRAMES,
        fps=fps,
        cwd=REPO_ROOT,
        crop_values=None,
        indexer=indexer,
    )
    # Named after the clip it indexes: an index keyed on the indexer alone
    # would be handed to whichever reference came second, and the source
    # filter rejects an index that does not match its file.
    suffix = indexer_spec(indexer).index_suffix
    reference_cache = tmp_path / f"{reference.stem}_ref{suffix}"
    load = indexer_spec(indexer).source_expr(str(reference), str(reference_cache))
    script = tmp_path / f"compare_{reference.stem}.vpy"
    write_vpy_script(
        script,
        sampling
        + _HASH_EPILOGUE.format(
            load=load,
            sampled_source=source.path.as_posix(),
            reference_source=reference.as_posix(),
        ),
    )

    proc = subprocess.run(
        [str(VS_PYTHON), str(script)],
        capture_output=True,
        text=True,
        env=VapourSynthEnv.from_cwd(REPO_ROOT).build_env(),
        check=False,
    )
    assert proc.returncode == 0, f"comparison script failed:\n{proc.stderr}"

    sampled: list[str] = []
    encoded: list[str] = []
    sampled_source = ""
    reference_source = ""
    for line in proc.stdout.splitlines():
        prefix, _, value = line.partition(":")
        if prefix == "SAMPLED":
            sampled.append(value)
        elif prefix == "REFERENCE":
            encoded.append(value)
        elif prefix == "SAMPLEDSOURCE":
            sampled_source = value
        elif prefix == "REFERENCESOURCE":
            reference_source = value
    return _Comparison(
        sampled=sampled,
        reference=encoded,
        sampled_source=sampled_source,
        reference_source=reference_source,
    )


def _first_difference(comparison: _Comparison) -> str | None:
    """Name the first frame where the two sides disagree.

    Args:
        comparison: Both hash lists.

    Returns:
        A message naming the frame and both hashes, or None when every frame
        matches and the two lists are the same length.
    """
    pairs = zip(comparison.sampled, comparison.reference, strict=False)
    for index, (sampled, encoded) in enumerate(pairs):
        if sampled != encoded:
            return f"frame {index} differs: sampled {sampled}, reference {encoded}"
    if len(comparison.sampled) != len(comparison.reference):
        return (
            f"frame counts differ: sampled {len(comparison.sampled)}, "
            f"reference {len(comparison.reference)}"
        )
    return None


def _check_both_sides_are_real(comparison: _Comparison) -> None:
    """Rule out the two ways this comparison could pass while proving nothing.

    Two empty lists compare equal, and a clip compared against itself matches
    whatever the sampler did. Neither is evidence, so both are refused before
    the hashes are looked at.

    Args:
        comparison: Both hash lists and the path each side read.

    Raises:
        AssertionError: If either side is empty, if fewer than one whole
            sample region came through, or if both sides read one file.
    """
    assert comparison.sampled_source != comparison.reference_source
    assert len(comparison.sampled) >= REGION_FRAMES
    assert len(comparison.reference) >= REGION_FRAMES


@requires_vapoursynth
@requires_x265
@requires_mkvmerge
@requires_libx265
@pytest.mark.parametrize("indexer", list(IndexerType))
@pytest.mark.parametrize("case", list(_FIXTURES))
def test_sampled_frames_are_the_frames_the_reference_holds(
    inputs: dict[str, Path], tmp_path: Path, case: str, indexer: IndexerType
) -> None:
    path = inputs[case]
    info = parse_video_info(path, log_hdr_metadata=False)
    source = _sampled_source(path, tmp_path, indexer)

    reference = encode_concatenated_reference(
        sources=[source],
        output_path=tmp_path / "reference.mkv",
        interval_frames=INTERVAL_FRAMES,
        region_frames=REGION_FRAMES,
        fps=info.fps,
        profile=LOSSLESS_PROFILE,
        video_info=info,
        indexer=indexer,
        cwd=REPO_ROOT,
        temp_dir=tmp_path,
    )

    comparison = _compare(source, reference, info.fps, tmp_path, indexer)
    _check_both_sides_are_real(comparison)

    assert _first_difference(comparison) is None


@requires_vapoursynth
@requires_x265
@requires_mkvmerge
@requires_libx265
def test_the_comparison_can_still_fail(inputs: dict[str, Path], tmp_path: Path) -> None:
    # Without this, six passing cases could mean six inert comparisons. Same
    # sampling, same encoder, same container, CRF 30 instead of lossless: the
    # only thing changed is whether the pixels survive the round trip.
    path = inputs["cfr"]
    info = parse_video_info(path, log_hdr_metadata=False)
    source = _sampled_source(path, tmp_path, IndexerType.FFMS2)

    distorted = encode_concatenated_distorted(
        sources=[source],
        output_path=tmp_path / "distorted.mkv",
        interval_frames=INTERVAL_FRAMES,
        region_frames=REGION_FRAMES,
        fps=info.fps,
        profile=LOSSLESS_PROFILE,
        crf=30.0,
        video_info=info,
        indexer=IndexerType.FFMS2,
        cwd=REPO_ROOT,
        temp_dir=tmp_path,
    )

    comparison = _compare(source, distorted, info.fps, tmp_path, IndexerType.FFMS2)
    _check_both_sides_are_real(comparison)

    assert _first_difference(comparison) is not None
