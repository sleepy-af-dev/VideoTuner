from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from videotuner.encoder_type import EncoderType
from videotuner.pipeline_types import get_profile_dir
from videotuner.profiles import Profile
from videotuner.ssimulacra2_assessment import (
    _calculate_stats_from_scores,  # pyright: ignore[reportPrivateUsage]
    ssim2_from_scores,
)

_PROFILE = Profile(
    name="Example Profile (x265)",
    description="Example",
    settings={},
    encoder=EncoderType.X265,
)


def test_scores_become_the_result_the_separate_pass_reports(tmp_path: Path) -> None:
    # The combined pass hands over per-frame scores rather than a result, and
    # must leave behind the same result and JSON log as the separate pass.
    scores = [80.0, 75.5, 90.25, 60.0]

    results = ssim2_from_scores(
        scores,
        workdir=tmp_path,
        profile=_PROFILE,
        iteration=3,
        log=logging.getLogger(__name__),
    )

    assert results == [_calculate_stats_from_scores(scores)]
    log_path = get_profile_dir(tmp_path, _PROFILE) / "ssim2_concatenated_iter3.json"
    logged = cast(dict[str, object], json.loads(log_path.read_text(encoding="utf-8")))
    assert logged["tool"] == "vszip"
    assert logged["count"] == 4
    assert logged["median"] == results[0].median
