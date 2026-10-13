"""Both indexers must decode a file to the same frames.

Frame equality is the strong form of the question the --indexer flag raises.
If the decoded planes are bit-identical and the frame properties match, every
downstream metric is identical by construction.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from videotuner.encoding_utils import IndexerType, indexer_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
VS_DIR = REPO_ROOT / "vapoursynth-portable"
VS_PYTHON = VS_DIR / "python.exe"
VS_PLUGINS = VS_DIR / "vs-plugins"
VSPIPE = VS_DIR / "Lib/site-packages/vapoursynth/vspipe.exe"

# ffmpeg muxes the test clip, so without it the test errors instead of skipping.
requires_vapoursynth = pytest.mark.skipif(
    not VS_PYTHON.exists() or shutil.which("ffmpeg") is None,
    reason="needs a provisioned vapoursynth-portable (tools/update_vs.py) and ffmpeg",
)

COMPARED_PROPS = ("_Matrix", "_Transfer", "_Primaries", "_Range")


def _make_clip(tmp_path: Path) -> Path:
    """Encode a short tagged clip both indexers can read.

    Renders a tagged clip with vspipe and muxes it through ffmpeg, since
    neither indexer plugin can index a raw y4m stream directly.

    Every frame is a different colour. A clip of identical frames cannot fail
    the comparison on temporal misalignment, which is the realistic way two
    demuxers disagree, and misalignment is the thing worth measuring: it would
    move a metric without changing a frame count. The frames are spliced one at
    a time because BlankClip paints one colour across whatever length it is
    given.

    Args:
        tmp_path: Scratch directory for the intermediate script and clip.

    Returns:
        Path to the encoded clip.
    """
    script = tmp_path / "make.vpy"
    _ = script.write_text(
        "import vapoursynth as vs\n"
        + "core = vs.core\n"
        + "frames = [core.std.BlankClip(format=vs.YUV420P10, width=320,\n"
        + "                             height=240, length=1,\n"
        + "                             color=[64 + i * 36, 512 + i * 8,\n"
        + "                                    512 - i * 8])\n"
        + "          for i in range(24)]\n"
        + "clip = core.std.Splice(frames)\n"
        + "clip = core.std.SetFrameProps(clip, _Matrix=1, _Transfer=1,\n"
        + "                              _Primaries=1, _Range=0)\n"
        + "clip.set_output()\n",
        encoding="utf-8",
    )
    out = tmp_path / "clip.mkv"
    raw = subprocess.run(
        [str(VSPIPE), "-c", "y4m", str(script), "-"],
        capture_output=True,
        check=True,
    ).stdout
    ffmpeg = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            "-",
            "-c:v",
            "libx264",
            # B-frames put the coded order out of step with the display order,
            # so the comparison covers PTS/DTS reordering rather than a stream
            # where the two are the same thing.
            "-bf",
            "2",
            str(out),
        ],
        input=raw,
        capture_output=True,
        check=False,
    )
    assert out.exists(), f"could not build the test clip: {ffmpeg.stderr!r}"
    return out


@requires_vapoursynth
def test_both_indexers_decode_the_same_frames(tmp_path: Path) -> None:
    """FFMS2 and LSMASHSource must agree pixel-for-pixel on the same file."""
    clip = _make_clip(tmp_path)

    caches = {
        i: tmp_path / f"idx{indexer_spec(i).index_suffix}"
        for i in (IndexerType.FFMS2, IndexerType.LSMASH)
    }
    loads = "\n".join(
        f"{name} = {indexer_spec(i).source_expr(str(clip), str(caches[i]))}"
        for name, i in (("a", IndexerType.FFMS2), ("b", IndexerType.LSMASH))
    )
    script = tmp_path / "compare.vpy"
    _ = script.write_text(
        "import vapoursynth as vs\n"
        + "core = vs.core\n"
        + f"{loads}\n"
        + 'assert len(a) == len(b), "frame counts differ"\n'
        + 'assert a.format.id == b.format.id, "formats differ"\n'
        + "pa, pb = a.get_frame(0).props, b.get_frame(0).props\n"
        + f"for k in {COMPARED_PROPS!r}:\n"
        + '    assert pa.get(k) == pb.get(k), k + " differs"\n'
        + 'diff = core.std.Expr([a, b], "x y - abs")\n'
        + "worst = 0.0\n"
        + "for i in (0, len(a) // 2, len(a) - 1):\n"
        + "    for p in range(diff.format.num_planes):\n"
        + "        s = core.std.PlaneStats(diff, plane=p)"
        + ".get_frame(i).props\n"
        + '        worst = max(worst, float(s["PlaneStatsMax"]))\n'
        + 'print("WORST:%r" % worst)\n',
        encoding="utf-8",
    )

    env = {**os.environ, "VAPOURSYNTH_EXTRA_PLUGIN_PATH": str(VS_PLUGINS)}
    proc = subprocess.run(
        [str(VS_PYTHON), str(script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"comparison failed:\n{proc.stderr}"
    # Parsed rather than matched as text: "WORST:0.0" is a prefix of "WORST:0.03".
    reported = re.search(r"WORST:(\S+)", proc.stdout)
    assert reported is not None, proc.stdout
    assert float(reported.group(1)) == 0.0, proc.stdout
