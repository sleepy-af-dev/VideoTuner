"""Tests for create_encodes module."""

from __future__ import annotations

import pytest

from videotuner.encoding_utils import CropValues, combine_crop_values
from videotuner.tool_parsers import CROPDETECT_RE


class TestCropValues:
    """Tests for CropValues dataclass."""

    def test_create_crop_values(self) -> None:
        """Test creating CropValues with all fields."""
        crop = CropValues(left=10, right=20, top=5, bottom=15)
        assert crop.left == 10
        assert crop.right == 20
        assert crop.top == 5
        assert crop.bottom == 15

    def test_frozen_dataclass(self) -> None:
        """Test CropValues is frozen (immutable)."""
        crop = CropValues(left=10, right=20, top=5, bottom=15)
        with pytest.raises(AttributeError):
            setattr(crop, "left", 100)


class TestCropdetectParsing:
    """Tests for CROPDETECT_RE regex."""

    def test_parse_crop_line(self) -> None:
        line = "[Parsed_cropdetect_0 @ 0x...] x1:0 x2:3839 y1:276 y2:1863 w:3840 h:1584 x:0 y:278 pts:1001 t:1.001000 limit:0.094118 crop=3840:1584:0:278"  # noqa: E501  # TODO(E501): shorten line
        matches: list[tuple[str, str, str, str]] = CROPDETECT_RE.findall(line)
        assert len(matches) == 1
        w, h, x, y = matches[0]
        assert (w, h, x, y) == ("3840", "1584", "0", "278")

    def test_parse_multiple_lines(self) -> None:
        output = "crop=3840:1584:0:278\ncrop=3840:1580:0:280\ncrop=3840:1584:0:278\n"
        matches: list[tuple[str, str, str, str]] = CROPDETECT_RE.findall(output)
        assert len(matches) == 3

    def test_no_match(self) -> None:
        output = "frame= 100 fps=50.0 q=28.0 size=N/A time=00:00:04.00"
        matches: list[tuple[str, str, str, str]] = CROPDETECT_RE.findall(output)
        assert len(matches) == 0


class TestCombineCropValues:
    """Reconciling crops when files in one source disagree on active area.

    Splicing needs every clip the same size, so the most aggressive crop wins
    on each edge independently.
    """

    def test_single_value_is_returned_unchanged(self) -> None:
        crop = CropValues(left=0, right=0, top=140, bottom=140)
        assert combine_crop_values([crop]) == crop

    def test_most_aggressive_edge_wins(self) -> None:
        a = CropValues(left=0, right=0, top=140, bottom=140)
        b = CropValues(left=0, right=0, top=132, bottom=132)

        assert combine_crop_values([a, b]) == a

    def test_edges_are_combined_independently(self) -> None:
        """The result can be a crop no single file measured."""
        a = CropValues(left=8, right=0, top=140, bottom=0)
        b = CropValues(left=0, right=12, top=0, bottom=136)

        combined = combine_crop_values([a, b])

        assert combined == CropValues(left=8, right=12, top=140, bottom=136)
        assert combined not in (a, b)

    def test_no_values_means_no_crop(self) -> None:
        assert combine_crop_values([]) == CropValues(left=0, right=0, top=0, bottom=0)
