"""Tests for utils module."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from videotuner.constants import MAX_USABLE_PATH, PATH_FILENAME_MARGIN
from videotuner.utils import (
    ensure_dir,
    fit_path_segment,
    job_folder_budget,
    parse_master_display_metadata,
)


class TestParseMasterDisplayMetadata:
    """Tests for master display metadata parsing."""

    def test_display_p3_parses_correctly(self):
        """Test Display P3 color space parsing."""
        primaries = "Display P3"
        luminance = "min: 0.0050 cd/m2, max: 1000 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is not None
        # Display P3 coordinates
        assert "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)" in result
        # Luminance: max=1000 -> 10000000, min=0.0050 -> 50
        assert result.endswith("L(10000000,50)")

    def test_bt2020_parses_correctly(self):
        """Test BT.2020 color space parsing."""
        primaries = "BT.2020"
        luminance = "min: 0.0001 cd/m2, max: 4000 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is not None
        # BT.2020 coordinates
        assert "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)" in result
        # Luminance: max=4000 -> 40000000, min=0.0001 -> 1
        assert result.endswith("L(40000000,1)")

    def test_dci_p3_parses_correctly(self):
        """Test DCI P3 color space parsing."""
        primaries = "DCI P3"
        luminance = "min: 0.0050 cd/m2, max: 1000 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is not None
        # DCI P3 has same primaries as Display P3 but different white point
        assert "G(13250,34500)B(7500,3000)R(34000,16000)WP(15700,17550)" in result
        assert result.endswith("L(10000000,50)")

    def test_unknown_color_space_returns_none(self):
        """Test that unknown color space returns None."""
        primaries = "Unknown Color Space"
        luminance = "min: 0.0050 cd/m2, max: 1000 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is None

    def test_invalid_luminance_format_returns_none(self):
        """Test that invalid luminance format returns None."""
        primaries = "Display P3"
        luminance = "invalid format"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is None

    def test_luminance_unit_conversion(self):
        """Test that luminance values are converted to x265 units (multiply by 10000)."""  # noqa: E501  # TODO(E501): shorten line
        primaries = "Display P3"
        luminance = "min: 0.0100 cd/m2, max: 500 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is not None
        # min: 0.01 * 10000 = 100
        # max: 500 * 10000 = 5000000
        assert result.endswith("L(5000000,100)")

    def test_zero_min_luminance(self):
        """Test handling of zero minimum luminance."""
        primaries = "BT.2020"
        luminance = "min: 0.0000 cd/m2, max: 1000 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is not None
        assert result.endswith("L(10000000,0)")

    def test_high_max_luminance(self):
        """Test handling of high maximum luminance (10000 nits)."""
        primaries = "BT.2020"
        luminance = "min: 0.0050 cd/m2, max: 10000 cd/m2"

        result = parse_master_display_metadata(primaries, luminance)

        assert result is not None
        assert result.endswith("L(100000000,50)")


class TestFitPathSegment:
    """Shortening a path segment so the files inside it stay under MAX_PATH."""

    def test_name_within_budget_is_untouched(self) -> None:
        assert fit_path_segment("short-input-name", 40) == "short-input-name"

    def test_oversized_name_is_cut_to_budget(self) -> None:
        fitted = fit_path_segment("x" * 200, 40)
        assert len(fitted) == 40

    def test_shortened_name_is_marked_and_keeps_a_readable_prefix(self) -> None:
        name = "a-long-example-input-filename-that-does-not-fit"
        fitted = fit_path_segment(name, 30)
        assert fitted.startswith("a-long-example-")
        assert "~" in fitted, "a shortened name should be visibly marked"

    def test_distinct_names_sharing_a_prefix_stay_distinct(self) -> None:
        prefix = "shared-leading-portion-of-two-input-names-"
        a = fit_path_segment(prefix + "first", 45)
        b = fit_path_segment(prefix + "second", 45)
        assert a != b, "truncation must not collapse two sources into one folder"

    def test_hash_is_stable_across_calls(self) -> None:
        name = "y" * 120
        assert fit_path_segment(name, 40) == fit_path_segment(name, 40)

    def test_does_not_interpret_the_name(self) -> None:
        """Truncation is deliberately dumb - no release-name parsing."""
        plain = fit_path_segment("a" * 100, 30)
        scene = fit_path_segment("b" * 100, 30)
        assert len(plain) == len(scene) == 30


class TestJobFolderBudget:
    """Room left for a job folder name under a given parent."""

    def test_deeper_parent_leaves_less_room(self, tmp_path: Path) -> None:
        shallow = job_folder_budget(tmp_path, ["Profile"])
        deeper = job_folder_budget(tmp_path / ("a" * 20), ["Profile"])
        assert deeper < shallow

    def test_longer_profile_slug_leaves_less_room(self, tmp_path: Path) -> None:
        short = job_folder_budget(tmp_path, ["P"])
        long = job_folder_budget(tmp_path, ["P" * 40])
        assert short - long == 39

    def test_longest_slug_wins(self, tmp_path: Path) -> None:
        assert job_folder_budget(tmp_path, ["P", "P" * 40]) == job_folder_budget(
            tmp_path, ["P" * 40]
        )

    def test_a_fitted_name_keeps_the_deepest_file_under_the_limit(
        self, tmp_path: Path
    ) -> None:
        slug = "Example Profile (x265)"
        budget = job_folder_budget(tmp_path, [slug])
        fitted = fit_path_segment("z" * 200, budget)
        deepest = (
            tmp_path / fitted / "ssimulacra2" / slug / "ssim2_concatenated_iter1.json"
        )
        assert len(str(deepest)) <= MAX_USABLE_PATH


class TestEnsureDirWarnsOnLongPaths:
    """The backstop for a directory too deep for any reasonable filename."""

    def test_no_warning_for_a_normal_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _ = ensure_dir(tmp_path / "short")
        assert not caplog.records

    def test_warns_once_for_an_over_budget_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        deep = tmp_path
        while len(str(deep)) < MAX_USABLE_PATH - PATH_FILENAME_MARGIN:
            deep = deep / ("segment_" + "q" * 20)

        with caplog.at_level(logging.WARNING):
            _ = ensure_dir(deep)
            _ = ensure_dir(deep)

        assert len(caplog.records) == 1, "should warn once per directory, not per call"
        assert "long-path aware" in caplog.records[0].message
