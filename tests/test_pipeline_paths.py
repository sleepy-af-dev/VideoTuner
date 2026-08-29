"""Tests for pipeline path management utilities and types."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from videotuner.encoder_type import EncoderType
from videotuner.pipeline_cli import PipelineArgs
from videotuner.pipeline_types import (
    IterationContext,
    get_profile_dir,
    get_reference_dir,
)
from videotuner.profiles import Profile


class TestGetReferenceDir:
    """Tests for reference directory path management."""

    def test_creates_reference_directory(self):
        """Test creates reference directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            ref_dir = get_reference_dir(workdir)

            assert ref_dir.exists()
            assert ref_dir.is_dir()
            assert ref_dir == workdir / "reference"

    def test_returns_existing_reference_directory(self):
        """Test returns existing reference directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            expected_dir = workdir / "reference"
            expected_dir.mkdir(parents=True, exist_ok=True)

            ref_dir = get_reference_dir(workdir)

            assert ref_dir == expected_dir
            assert ref_dir.exists()

    def test_creates_parent_directories(self):
        """Test creates parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir) / "nested" / "path"
            ref_dir = get_reference_dir(workdir)

            assert ref_dir.exists()
            assert workdir.exists()


class TestGetDistortedDir:
    """Tests for distorted directory path management."""

    def test_creates_distorted_directory_for_profile(self):
        """Test creates distorted directory with profile name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="TestProfile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            dist_dir = get_profile_dir(workdir, profile)

            assert dist_dir.exists()
            assert dist_dir.is_dir()
            assert dist_dir == workdir / "TestProfile"

    def test_sanitizes_profile_name_with_spaces(self):
        """Test replaces spaces in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Film High Quality",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            dist_dir = get_profile_dir(workdir, profile)

            assert dist_dir == workdir / "Film_High_Quality"
            assert dist_dir.exists()

    def test_sanitizes_profile_name_with_forward_slash(self):
        """Test replaces forward slashes in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Film/Animation",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            dist_dir = get_profile_dir(workdir, profile)

            assert dist_dir == workdir / "Film_Animation"
            assert dist_dir.exists()

    def test_sanitizes_profile_name_with_backslash(self):
        """Test replaces backslashes in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Film\\Animation",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            dist_dir = get_profile_dir(workdir, profile)

            # Backslash should be replaced with underscore
            assert "Film_Animation" in str(dist_dir)
            assert dist_dir.exists()

    def test_returns_existing_distorted_directory(self):
        """Test returns existing distorted directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="TestProfile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )
            expected_dir = workdir / "TestProfile"
            expected_dir.mkdir(parents=True, exist_ok=True)

            dist_dir = get_profile_dir(workdir, profile)

            assert dist_dir == expected_dir
            assert dist_dir.exists()


class TestGetVmafDir:
    """Tests for VMAF directory path management."""

    def test_creates_vmaf_directory_for_profile(self):
        """Test creates VMAF directory with profile name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="TestProfile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            vmaf_dir = get_profile_dir(workdir, profile)

            assert vmaf_dir.exists()
            assert vmaf_dir.is_dir()
            assert vmaf_dir == workdir / "TestProfile"

    def test_sanitizes_profile_name_with_spaces(self):
        """Test replaces spaces in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Film High Quality",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            vmaf_dir = get_profile_dir(workdir, profile)

            assert vmaf_dir == workdir / "Film_High_Quality"
            assert vmaf_dir.exists()

    def test_sanitizes_profile_name_with_forward_slash(self):
        """Test replaces forward slashes in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Film/Animation",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            vmaf_dir = get_profile_dir(workdir, profile)

            assert vmaf_dir == workdir / "Film_Animation"
            assert vmaf_dir.exists()

    def test_sanitizes_profile_name_with_backslash(self):
        """Test replaces backslashes in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Test\\Profile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            vmaf_dir = get_profile_dir(workdir, profile)

            assert "Test_Profile" in str(vmaf_dir)
            assert vmaf_dir.exists()

    def test_returns_existing_vmaf_directory(self):
        """Test returns existing VMAF directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="TestProfile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )
            expected_dir = workdir / "TestProfile"
            expected_dir.mkdir(parents=True, exist_ok=True)

            vmaf_dir = get_profile_dir(workdir, profile)

            assert vmaf_dir == expected_dir
            assert vmaf_dir.exists()


class TestGetSsim2Dir:
    """Tests for SSIMULACRA2 directory path management."""

    def test_creates_ssim2_directory_for_profile(self):
        """Test creates SSIMULACRA2 directory with profile name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="TestProfile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            ssim2_dir = get_profile_dir(workdir, profile)

            assert ssim2_dir.exists()
            assert ssim2_dir.is_dir()
            assert ssim2_dir == workdir / "TestProfile"

    def test_sanitizes_profile_name_with_spaces(self):
        """Test replaces spaces in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Animation Ultra",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            ssim2_dir = get_profile_dir(workdir, profile)

            assert ssim2_dir == workdir / "Animation_Ultra"
            assert ssim2_dir.exists()

    def test_sanitizes_profile_name_with_forward_slash(self):
        """Test replaces forward slashes in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="CGI/Anime",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            ssim2_dir = get_profile_dir(workdir, profile)

            assert ssim2_dir == workdir / "CGI_Anime"
            assert ssim2_dir.exists()

    def test_sanitizes_profile_name_with_backslash(self):
        """Test replaces backslashes in profile name with underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Test\\Profile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            ssim2_dir = get_profile_dir(workdir, profile)

            assert "Test_Profile" in str(ssim2_dir)
            assert ssim2_dir.exists()

    def test_returns_existing_ssim2_directory(self):
        """Test returns existing SSIMULACRA2 directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="TestProfile",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )
            expected_dir = workdir / "TestProfile"
            expected_dir.mkdir(parents=True, exist_ok=True)

            ssim2_dir = get_profile_dir(workdir, profile)

            assert ssim2_dir == expected_dir
            assert ssim2_dir.exists()


class TestPathConsistency:
    """Tests for consistent behavior across all path functions."""

    def test_all_functions_create_directories(self):
        """Test all functions create their directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Test", description="Test", settings={}, encoder=EncoderType.X265
            )

            ref_dir = get_reference_dir(workdir)
            dist_dir = get_profile_dir(workdir, profile)
            vmaf_dir = get_profile_dir(workdir, profile)
            ssim2_dir = get_profile_dir(workdir, profile)

            assert ref_dir.exists()
            assert dist_dir.exists()
            assert vmaf_dir.exists()
            assert ssim2_dir.exists()

    def test_all_functions_handle_same_profile_consistently(self):
        """Test all functions handle the same profile consistently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = Profile(
                name="Film/High Quality",
                description="Test",
                settings={},
                encoder=EncoderType.X265,
            )

            dist_dir = get_profile_dir(workdir, profile)
            vmaf_dir = get_profile_dir(workdir, profile)
            ssim2_dir = get_profile_dir(workdir, profile)

            # All should sanitize the profile name the same way
            assert "Film_High_Quality" in str(dist_dir)
            assert "Film_High_Quality" in str(vmaf_dir)
            assert "Film_High_Quality" in str(ssim2_dir)


class TestIterationContext:
    """Tests for IterationContext dataclass."""

    def test_sharing_samples_defaults_to_false(self):
        """Test that sharing_samples defaults to False."""
        ctx = IterationContext(
            input_path=Path("test.mkv"),
            sources=[],
            workdir=Path("/tmp/work"),
            temp_dir=Path("/tmp/temp"),
            repo_root=Path("/repo"),
            info=MagicMock(),
            selected_profile=Profile(
                name="Test", description="Test", settings={}, encoder=EncoderType.X265
            ),
            total_frames=10000,
            guard_start_frames=100,
            guard_end_frames=100,
            vmaf_ref_path=None,
            ssim2_ref_path=None,
            args=PipelineArgs(input=Path("test.mkv")),
            display=MagicMock(),
            log=logging.getLogger("test"),
        )
        assert ctx.sharing_samples is False

    def test_sharing_samples_can_be_set_true(self):
        """Test that sharing_samples can be set to True."""
        ctx = IterationContext(
            input_path=Path("test.mkv"),
            sources=[],
            workdir=Path("/tmp/work"),
            temp_dir=Path("/tmp/temp"),
            repo_root=Path("/repo"),
            info=MagicMock(),
            selected_profile=Profile(
                name="Test", description="Test", settings={}, encoder=EncoderType.X265
            ),
            total_frames=10000,
            guard_start_frames=100,
            guard_end_frames=100,
            vmaf_ref_path=None,
            ssim2_ref_path=None,
            args=PipelineArgs(input=Path("test.mkv")),
            display=MagicMock(),
            log=logging.getLogger("test"),
            sharing_samples=True,
        )
        assert ctx.sharing_samples is True

    def test_usable_frames_calculation(self):
        """Test usable_frames property excludes guard bands."""
        ctx = IterationContext(
            input_path=Path("test.mkv"),
            sources=[],
            workdir=Path("/tmp/work"),
            temp_dir=Path("/tmp/temp"),
            repo_root=Path("/repo"),
            info=MagicMock(),
            selected_profile=Profile(
                name="Test", description="Test", settings={}, encoder=EncoderType.X265
            ),
            total_frames=10000,
            guard_start_frames=100,
            guard_end_frames=200,
            vmaf_ref_path=None,
            ssim2_ref_path=None,
            args=PipelineArgs(input=Path("test.mkv")),
            display=MagicMock(),
            log=logging.getLogger("test"),
        )
        # usable = 10000 - 100 - 200 = 9700
        assert ctx.usable_frames == 9700

    def test_usable_frames_with_no_guards(self):
        """Test usable_frames equals total when no guard bands."""
        ctx = IterationContext(
            input_path=Path("test.mkv"),
            sources=[],
            workdir=Path("/tmp/work"),
            temp_dir=Path("/tmp/temp"),
            repo_root=Path("/repo"),
            info=MagicMock(),
            selected_profile=Profile(
                name="Test", description="Test", settings={}, encoder=EncoderType.X265
            ),
            total_frames=10000,
            guard_start_frames=0,
            guard_end_frames=0,
            vmaf_ref_path=None,
            ssim2_ref_path=None,
            args=PipelineArgs(input=Path("test.mkv")),
            display=MagicMock(),
            log=logging.getLogger("test"),
        )
        assert ctx.usable_frames == 10000

    def test_crop_values_defaults_to_none(self):
        """Test that crop_values defaults to None."""
        ctx = IterationContext(
            input_path=Path("test.mkv"),
            sources=[],
            workdir=Path("/tmp/work"),
            temp_dir=Path("/tmp/temp"),
            repo_root=Path("/repo"),
            info=MagicMock(),
            selected_profile=Profile(
                name="Test", description="Test", settings={}, encoder=EncoderType.X265
            ),
            total_frames=10000,
            guard_start_frames=100,
            guard_end_frames=100,
            vmaf_ref_path=None,
            ssim2_ref_path=None,
            args=PipelineArgs(input=Path("test.mkv")),
            display=MagicMock(),
            log=logging.getLogger("test"),
        )
        assert ctx.crop_values is None


class TestReservedProfileNames:
    """A profile directory sits beside reference/ and temp/, so names must differ."""

    @staticmethod
    def _profile(name: str) -> Profile:
        return Profile(
            name=name, description="Test", settings={}, encoder=EncoderType.X265
        )

    def test_profile_named_reference_does_not_collide(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            ref_dir = get_reference_dir(workdir)
            prof_dir = get_profile_dir(workdir, self._profile("reference"))

            assert prof_dir != ref_dir
            assert prof_dir.exists()

    def test_profile_named_temp_does_not_collide(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            prof_dir = get_profile_dir(workdir, self._profile("temp"))

            assert prof_dir.name != "temp"

    def test_guard_is_case_insensitive(self):
        """Windows treats Reference and reference as one directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            prof_dir = get_profile_dir(workdir, self._profile("Reference"))

            assert prof_dir != get_reference_dir(workdir)

    def test_ordinary_names_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            prof_dir = get_profile_dir(workdir, self._profile("Film"))

            assert prof_dir == workdir / "Film"

    def test_all_three_helpers_share_one_directory(self):
        """Encodes and both metrics live together; filenames already differ."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            profile = self._profile("Film")

            assert (
                get_profile_dir(workdir, profile)
                == get_profile_dir(workdir, profile)
                == get_profile_dir(workdir, profile)
            )
