"""Tests for encoding utilities module."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from videotuner.encoder_type import EncoderType
from videotuner.encoding_utils import (
    HDR_TRANSFER_CHARACTERISTICS,
    VS_PACKAGE_SUBDIR,
    CropValues,
    EncoderPaths,
    IndexerType,
    SampledSource,
    UsableRange,
    VapourSynthEnv,
    build_encoder_command,
    build_sampling_vpy_script,
    build_vspipe_command,
    build_x264_command,
    build_x265_command,
    calculate_usable_frames,
    combine_crop_values,
    create_temp_encode_paths,
    distorted_index_path,
    get_encoder_bin,
    get_vapoursynth_portable_dir,
    indexer_spec,
    is_hdr_video,
    read_indexed_frame_count,
    resolve_absolute_path,
    run_vspipe_encode,
)


class TestHDRDetection:
    """Tests for HDR transfer characteristic detection."""

    def test_pq_transfer_is_hdr(self):
        """Test PQ transfer is detected as HDR."""
        assert is_hdr_video("pq") is True

    def test_smpte2084_transfer_is_hdr(self):
        """Test SMPTE 2084 transfer is detected as HDR."""
        assert is_hdr_video("smpte2084") is True

    def test_smpte2084_with_spaces_is_hdr(self):
        """Test SMPTE 2084 with spaces is detected as HDR."""
        assert is_hdr_video("smpte 2084") is True

    def test_hlg_transfer_is_hdr(self):
        """Test HLG transfer is detected as HDR."""
        assert is_hdr_video("hlg") is True

    def test_arib_std_b67_transfer_is_hdr(self):
        """Test ARIB STD-B67 transfer is detected as HDR."""
        assert is_hdr_video("arib-std-b67") is True

    def test_arib_std_b67_with_space_is_hdr(self):
        """Test ARIB STD-B67 with space is detected as HDR."""
        assert is_hdr_video("arib std-b67") is True

    def test_bt709_transfer_is_not_hdr(self):
        """Test BT.709 transfer is not HDR."""
        assert is_hdr_video("bt709") is False

    def test_none_transfer_is_not_hdr(self):
        """Test None transfer is not HDR."""
        assert is_hdr_video(None) is False

    def test_empty_string_is_not_hdr(self):
        """Test empty string is not HDR."""
        assert is_hdr_video("") is False

    def test_unknown_transfer_is_not_hdr(self):
        """Test unknown transfer is not HDR."""
        assert is_hdr_video("unknown") is False

    def test_case_insensitive_pq(self):
        """Test PQ detection is case insensitive."""
        assert is_hdr_video("PQ") is True
        assert is_hdr_video("Pq") is True
        assert is_hdr_video("pQ") is True

    def test_case_insensitive_hlg(self):
        """Test HLG detection is case insensitive."""
        assert is_hdr_video("HLG") is True
        assert is_hdr_video("Hlg") is True

    def test_hdr_transfer_characteristics_constant(self):
        """Test HDR_TRANSFER_CHARACTERISTICS contains expected values."""
        assert "pq" in HDR_TRANSFER_CHARACTERISTICS
        assert "smpte2084" in HDR_TRANSFER_CHARACTERISTICS
        assert "smpte 2084" in HDR_TRANSFER_CHARACTERISTICS
        assert "hlg" in HDR_TRANSFER_CHARACTERISTICS
        assert "arib-std-b67" in HDR_TRANSFER_CHARACTERISTICS
        assert "arib std-b67" in HDR_TRANSFER_CHARACTERISTICS


class TestGetEncoderBin:
    """Tests for encoder binary path resolution."""

    def test_x265_returns_relative_path_when_no_cwd(self):
        """Test returns relative path when cwd is None."""
        result = get_encoder_bin(EncoderType.X265, cwd=None)
        assert result == Path("tools") / "x265.exe"
        assert not result.is_absolute()

    def test_x265_returns_absolute_path_when_cwd_provided(self):
        """Test returns absolute path when cwd is provided."""
        cwd = Path("C:/test/dir")
        result = get_encoder_bin(EncoderType.X265, cwd=cwd)
        assert result == Path("C:/test/dir") / "tools" / "x265.exe"

    def test_x264_returns_relative_path_when_no_cwd(self):
        """Test x264 returns relative path when cwd is None."""
        result = get_encoder_bin(EncoderType.X264, cwd=None)
        assert result == Path("tools") / "x264.exe"
        assert not result.is_absolute()

    def test_x264_returns_absolute_path_when_cwd_provided(self):
        """Test x264 returns absolute path when cwd is provided."""
        cwd = Path("C:/test/dir")
        result = get_encoder_bin(EncoderType.X264, cwd=cwd)
        assert result == Path("C:/test/dir") / "tools" / "x264.exe"

    def test_preserves_cwd_path_type(self):
        """Test preserves the type of cwd (Path or string-like)."""
        cwd = Path("/home/user/project")
        result = get_encoder_bin(EncoderType.X265, cwd=cwd)
        assert isinstance(result, Path)
        # Check the path ends with the expected components (platform-agnostic)
        assert result.name == "x265.exe"
        assert result.parent.name == "tools"


class TestGetVapourSynthPortableDir:
    """Tests for VapourSynth portable directory path resolution."""

    def test_returns_relative_path_when_no_cwd(self):
        """Test returns relative path when cwd is None."""
        result = get_vapoursynth_portable_dir(cwd=None)
        assert result == Path("vapoursynth-portable")
        assert not result.is_absolute()

    def test_returns_absolute_path_when_cwd_provided(self):
        """Test returns absolute path when cwd is provided."""
        cwd = Path("C:/test/dir")
        result = get_vapoursynth_portable_dir(cwd=cwd)
        assert result == Path("C:/test/dir") / "vapoursynth-portable"

    def test_preserves_cwd_path_type(self):
        """Test preserves the type of cwd."""
        cwd = Path("/opt/videotuner")
        result = get_vapoursynth_portable_dir(cwd=cwd)
        assert isinstance(result, Path)
        # Check the path ends with the expected component (platform-agnostic)
        assert result.name == "vapoursynth-portable"


class TestResolveAbsolutePath:
    """Tests for absolute path resolution."""

    def test_returns_absolute_path_unchanged(self):
        """Test absolute paths are returned unchanged."""
        # Use a fully qualified path that works on both Windows and Unix
        abs_path = Path("C:/absolute/path/to/file.mkv").resolve()
        result = resolve_absolute_path(abs_path, cwd=None)
        assert result == abs_path
        assert result.is_absolute()

    def test_resolves_relative_path_with_cwd(self):
        """Test relative paths are resolved relative to cwd."""
        rel_path = Path("videos/input.mkv")
        cwd = Path("/home/user/project")
        result = resolve_absolute_path(rel_path, cwd=cwd)
        assert result == Path("/home/user/project") / "videos" / "input.mkv"

    def test_resolves_relative_path_without_cwd(self):
        """Test relative paths are resolved without cwd (uses resolve())."""
        rel_path = Path("test.mkv")
        result = resolve_absolute_path(rel_path, cwd=None)
        # Should call resolve() which makes it absolute
        assert result.is_absolute()

    def test_handles_windows_absolute_path(self):
        """Test Windows absolute paths are returned unchanged."""
        win_path = Path("C:/Users/test/video.mkv")
        result = resolve_absolute_path(win_path, cwd=None)
        assert result == win_path


class TestCreateTempEncodePaths:
    """Tests for temporary encode path creation."""

    def test_creates_x265_paths_in_system_temp_by_default(self):
        """Test creates x265 paths in system temp when temp_dir is None."""
        vpy_path, bitstream_path = create_temp_encode_paths(
            EncoderType.X265, temp_dir=None
        )

        # Should be in system temp directory
        assert vpy_path.suffix == ".vpy"
        assert bitstream_path.suffix == ".hevc"
        assert vpy_path.is_absolute()
        assert bitstream_path.is_absolute()

        # Clean up
        try:
            vpy_path.unlink(missing_ok=True)
            bitstream_path.unlink(missing_ok=True)
        except OSError:  # Best-effort cleanup; ignore failures in tests
            pass

    def test_creates_x264_paths_with_correct_extension(self):
        """Test creates x264 paths with .264 extension."""
        vpy_path, bitstream_path = create_temp_encode_paths(
            EncoderType.X264, temp_dir=None
        )

        assert vpy_path.suffix == ".vpy"
        assert bitstream_path.suffix == ".264"

        # Clean up
        try:
            vpy_path.unlink(missing_ok=True)
            bitstream_path.unlink(missing_ok=True)
        except OSError:  # Best-effort cleanup; ignore failures in tests
            pass

    def test_creates_paths_with_name_in_system_temp(self):
        """Test name is used as prefix when temp_dir is None."""
        vpy_path, bitstream_path = create_temp_encode_paths(
            EncoderType.X265, temp_dir=None, name="mytest"
        )

        # When using system temp, name is used as prefix, but files still use random names  # noqa: E501  # TODO(E501): shorten line
        # Just verify they have the right extensions and the prefix is included
        assert vpy_path.suffix == ".vpy"
        assert bitstream_path.suffix == ".hevc"
        assert "mytest" in vpy_path.name

        # Clean up
        try:
            vpy_path.unlink(missing_ok=True)
            bitstream_path.unlink(missing_ok=True)
        except OSError:  # Best-effort cleanup; ignore failures in tests
            pass

    def test_creates_paths_in_custom_temp_dir(self):
        """Test creates paths in custom temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            vpy_path, bitstream_path = create_temp_encode_paths(
                EncoderType.X265, temp_dir=temp_dir
            )

            assert vpy_path.parent == temp_dir
            assert bitstream_path.parent == temp_dir
            assert vpy_path.name == "encode.vpy"
            assert bitstream_path.name == "encode.hevc"

    def test_creates_x264_paths_in_custom_temp_dir(self):
        """Test creates x264 paths with .264 extension in custom directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            vpy_path, bitstream_path = create_temp_encode_paths(
                EncoderType.X264, temp_dir=temp_dir
            )

            assert vpy_path.parent == temp_dir
            assert bitstream_path.parent == temp_dir
            assert vpy_path.name == "encode.vpy"
            assert bitstream_path.name == "encode.264"

    def test_creates_paths_in_custom_temp_dir_with_custom_name(self):
        """Test creates paths in custom directory with custom name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            vpy_path, bitstream_path = create_temp_encode_paths(
                EncoderType.X265, temp_dir=temp_dir, name="iteration_5"
            )

            assert vpy_path.parent == temp_dir
            assert bitstream_path.parent == temp_dir
            assert vpy_path.name == "iteration_5.vpy"
            assert bitstream_path.name == "iteration_5.hevc"

    def test_creates_temp_dir_if_not_exists(self):
        """Test creates temp directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir) / "subdir" / "nested"
            assert not temp_dir.exists()

            vpy_path, bitstream_path = create_temp_encode_paths(
                EncoderType.X265, temp_dir=temp_dir
            )

            assert temp_dir.exists()
            assert vpy_path.parent == temp_dir
            assert bitstream_path.parent == temp_dir

    def test_returns_tuple_of_paths(self):
        """Test returns tuple of (vpy_path, bitstream_path)."""
        result = create_temp_encode_paths(EncoderType.X265, temp_dir=None)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], Path)
        assert isinstance(result[1], Path)

        # Clean up
        try:
            result[0].unlink(missing_ok=True)
            result[1].unlink(missing_ok=True)
        except OSError:  # Best-effort cleanup; ignore failures in tests
            pass


class TestVapourSynthEnv:
    """Tests for VapourSynthEnv dataclass."""

    def test_from_cwd_with_none(self):
        """Test from_cwd returns relative paths when cwd is None."""
        env = VapourSynthEnv.from_cwd(None)
        assert env.vs_dir == Path("vapoursynth-portable")
        assert (
            env.vsscript_dll
            == Path("vapoursynth-portable") / VS_PACKAGE_SUBDIR / "vsscript.dll"
        )
        assert (
            env.ffms2_dll == Path("vapoursynth-portable") / "vs-plugins" / "ffms2.dll"
        )
        assert env.vs_plugin_dir == Path("vapoursynth-portable") / "vs-plugins"
        assert (
            env.vspipe_bin
            == Path("vapoursynth-portable") / VS_PACKAGE_SUBDIR / "vspipe.exe"
        )

    def test_from_cwd_with_path(self):
        """Test from_cwd returns paths relative to cwd."""
        cwd = Path("C:/project")
        env = VapourSynthEnv.from_cwd(cwd)
        assert env.vs_dir == Path("C:/project/vapoursynth-portable")
        assert (
            env.vsscript_dll
            == Path("C:/project/vapoursynth-portable")
            / VS_PACKAGE_SUBDIR
            / "vsscript.dll"
        )
        assert env.ffms2_dll == Path(
            "C:/project/vapoursynth-portable/vs-plugins/ffms2.dll"
        )
        assert env.vs_plugin_dir == Path("C:/project/vapoursynth-portable/vs-plugins")
        assert (
            env.vspipe_bin
            == Path("C:/project/vapoursynth-portable")
            / VS_PACKAGE_SUBDIR
            / "vspipe.exe"
        )

    def test_from_args_uses_provided_paths(self):
        """Test from_args uses CLI-provided paths."""
        vs_dir = Path("C:/custom/vs")
        vs_plugin_dir = Path("C:/custom/plugins")
        env = VapourSynthEnv.from_args(vs_dir, vs_plugin_dir, Path("C:/repo"))
        assert env.vs_dir == Path("C:/custom/vs")
        assert env.vs_plugin_dir == Path("C:/custom/plugins")

    def test_from_args_falls_back_to_repo_root(self):
        """Test from_args falls back to repo root when args are None."""
        env = VapourSynthEnv.from_args(None, None, Path("C:/repo"))
        assert env.vs_dir == Path("C:/repo/vapoursynth-portable")
        assert env.vs_plugin_dir == Path("C:/repo/vapoursynth-portable/vs-plugins")

    def test_validate_raises_when_vsscript_missing(self):
        """Test validate raises FileNotFoundError when vsscript.dll is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            env = VapourSynthEnv.from_cwd(cwd)

            with pytest.raises(FileNotFoundError) as exc_info:
                env.validate()
            assert "vsscript.dll" in str(exc_info.value)

    def test_validate_raises_when_ffms2_missing(self):
        """Test validate raises FileNotFoundError when ffms2.dll is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            vs_dir = cwd / "vapoursynth-portable"
            vs_dir.mkdir(parents=True)
            package_dir = vs_dir / VS_PACKAGE_SUBDIR
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "vsscript.dll").touch()

            env = VapourSynthEnv.from_cwd(cwd)

            with pytest.raises(FileNotFoundError) as exc_info:
                env.validate()
            assert "ffms2.dll" in str(exc_info.value)

    def test_validate_raises_when_lsmash_missing(self):
        """Test validate raises FileNotFoundError when LSMASHSource.dll is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            vs_dir = cwd / "vapoursynth-portable"
            plugins_dir = vs_dir / "vs-plugins"
            plugins_dir.mkdir(parents=True)
            package_dir = vs_dir / VS_PACKAGE_SUBDIR
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "vsscript.dll").touch()
            (plugins_dir / "ffms2.dll").touch()

            env = VapourSynthEnv.from_cwd(cwd)

            with pytest.raises(FileNotFoundError) as exc_info:
                env.validate()
            assert "LSMASHSource.dll" in str(exc_info.value)

    def test_validate_raises_when_vspipe_missing(self):
        """Test validate raises FileNotFoundError when vspipe.exe is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            vs_dir = cwd / "vapoursynth-portable"
            plugins_dir = vs_dir / "vs-plugins"
            plugins_dir.mkdir(parents=True)
            package_dir = vs_dir / VS_PACKAGE_SUBDIR
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "vsscript.dll").touch()
            (plugins_dir / "ffms2.dll").touch()
            (plugins_dir / "LSMASHSource.dll").touch()

            env = VapourSynthEnv.from_cwd(cwd)

            with pytest.raises(FileNotFoundError) as exc_info:
                env.validate()
            assert "vspipe" in str(exc_info.value)

    def test_validate_passes_when_files_exist(self):
        """Test validate passes when all required files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            vs_dir = cwd / "vapoursynth-portable"
            plugins_dir = vs_dir / "vs-plugins"
            plugins_dir.mkdir(parents=True)
            package_dir = vs_dir / VS_PACKAGE_SUBDIR
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "vsscript.dll").touch()
            (package_dir / "vspipe.exe").touch()
            (plugins_dir / "ffms2.dll").touch()
            (plugins_dir / "LSMASHSource.dll").touch()

            env = VapourSynthEnv.from_cwd(cwd)
            # Should not raise
            env.validate()

    def test_build_env_sets_vapoursynth_portable(self):
        """Test build_env sets VAPOURSYNTH_PORTABLE environment variable."""
        vs_env = VapourSynthEnv.from_cwd(Path("C:/project"))
        env = vs_env.build_env({})
        assert "VAPOURSYNTH_PORTABLE" in env
        assert env["VAPOURSYNTH_PORTABLE"] == str(
            Path("C:/project/vapoursynth-portable")
        )

    def test_build_env_prepends_to_path(self):
        """Test build_env prepends VS directory to PATH."""
        vs_env = VapourSynthEnv.from_cwd(Path("C:/project"))
        base_env = {"PATH": "/usr/bin", "HOME": "/home/user"}
        env = vs_env.build_env(base_env)
        assert env["PATH"].startswith(str(Path("C:/project/vapoursynth-portable")))
        assert "/usr/bin" in env["PATH"]
        assert env["HOME"] == "/home/user"

    def test_frozen_dataclass(self):
        """Test VapourSynthEnv is frozen (immutable)."""
        env = VapourSynthEnv.from_cwd(None)
        with pytest.raises(AttributeError):
            setattr(env, "vs_dir", Path("new/path"))


class TestEncoderPaths:
    """Tests for EncoderPaths dataclass."""

    def test_from_cwd_x265_with_none(self):
        """Test from_cwd returns relative x265 paths when cwd is None."""
        paths = EncoderPaths.from_cwd(None, EncoderType.X265)
        assert paths.encoder_bin == Path("tools") / "x265.exe"
        assert paths.encoder_type == EncoderType.X265
        assert paths.vs_env.vs_dir == Path("vapoursynth-portable")

    def test_from_cwd_x264_with_none(self):
        """Test from_cwd returns relative x264 paths when cwd is None."""
        paths = EncoderPaths.from_cwd(None, EncoderType.X264)
        assert paths.encoder_bin == Path("tools") / "x264.exe"
        assert paths.encoder_type == EncoderType.X264

    def test_from_cwd_with_path(self):
        """Test from_cwd returns paths relative to cwd."""
        cwd = Path("C:/project")
        paths = EncoderPaths.from_cwd(cwd, EncoderType.X265)
        assert paths.encoder_bin == Path("C:/project/tools/x265.exe")
        assert paths.vs_env.vs_dir == Path("C:/project/vapoursynth-portable")

    def test_validate_raises_when_encoder_missing(self):
        """Test validate raises FileNotFoundError when encoder binary is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            paths = EncoderPaths.from_cwd(cwd, EncoderType.X265)

            with pytest.raises(FileNotFoundError) as exc_info:
                paths.validate()
            assert "x265" in str(exc_info.value)

    def test_validate_raises_when_vapoursynth_missing(self):
        """Test validate raises FileNotFoundError when VapourSynth is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            tools_dir = cwd / "tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "x265.exe").touch()

            paths = EncoderPaths.from_cwd(cwd, EncoderType.X265)

            with pytest.raises(FileNotFoundError) as exc_info:
                paths.validate()
            # Should fail on VapourSynth validation
            assert "vsscript.dll" in str(exc_info.value)

    def test_validate_passes_when_all_files_exist(self):
        """Test validate passes when all required files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            # Create encoder
            tools_dir = cwd / "tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "x265.exe").touch()
            # Create VapourSynth
            vs_dir = cwd / "vapoursynth-portable"
            plugins_dir = vs_dir / "vs-plugins"
            plugins_dir.mkdir(parents=True)
            package_dir = vs_dir / VS_PACKAGE_SUBDIR
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "vsscript.dll").touch()
            (package_dir / "vspipe.exe").touch()
            (plugins_dir / "ffms2.dll").touch()
            (plugins_dir / "LSMASHSource.dll").touch()

            paths = EncoderPaths.from_cwd(cwd, EncoderType.X265)
            # Should not raise
            paths.validate()

    def test_nested_vs_env_accessible(self):
        """Test that VapourSynthEnv is accessible via vs_env."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X265)
        assert isinstance(paths.vs_env, VapourSynthEnv)
        assert (
            paths.vs_env.vsscript_dll
            == Path("C:/project/vapoursynth-portable")
            / VS_PACKAGE_SUBDIR
            / "vsscript.dll"
        )

    def test_frozen_dataclass(self):
        """Test EncoderPaths is frozen (immutable)."""
        paths = EncoderPaths.from_cwd(None, EncoderType.X265)
        with pytest.raises(AttributeError):
            setattr(paths, "encoder_bin", Path("new/path"))


class TestCalculateUsableFrames:
    """Tests for calculate_usable_frames function."""

    def test_calculates_usable_frames(self):
        """Test basic usable frames calculation."""
        result = calculate_usable_frames(
            total_frames=1000,
            guard_start_frames=100,
            guard_end_frames=100,
        )
        assert result == 800

    def test_no_guards(self):
        """Test with zero guard frames."""
        result = calculate_usable_frames(
            total_frames=1000,
            guard_start_frames=0,
            guard_end_frames=0,
        )
        assert result == 1000

    def test_asymmetric_guards(self):
        """Test with different start and end guards."""
        result = calculate_usable_frames(
            total_frames=1000,
            guard_start_frames=50,
            guard_end_frames=150,
        )
        assert result == 800

    def test_raises_when_total_frames_zero(self):
        """Test raises ValueError when total_frames is zero."""
        with pytest.raises(ValueError) as exc_info:
            _ = calculate_usable_frames(
                total_frames=0,
                guard_start_frames=0,
                guard_end_frames=0,
            )
        assert "total_frames must be >= 1" in str(exc_info.value)

    def test_raises_when_total_frames_negative(self):
        """Test raises ValueError when total_frames is negative."""
        with pytest.raises(ValueError) as exc_info:
            _ = calculate_usable_frames(
                total_frames=-10,
                guard_start_frames=0,
                guard_end_frames=0,
            )
        assert "total_frames must be >= 1" in str(exc_info.value)

    def test_raises_when_guard_start_negative(self):
        """Test raises ValueError when guard_start_frames is negative."""
        with pytest.raises(ValueError) as exc_info:
            _ = calculate_usable_frames(
                total_frames=1000,
                guard_start_frames=-10,
                guard_end_frames=0,
            )
        assert "guard_start_frames must be >= 0" in str(exc_info.value)

    def test_raises_when_guard_end_negative(self):
        """Test raises ValueError when guard_end_frames is negative."""
        with pytest.raises(ValueError) as exc_info:
            _ = calculate_usable_frames(
                total_frames=1000,
                guard_start_frames=0,
                guard_end_frames=-10,
            )
        assert "guard_end_frames must be >= 0" in str(exc_info.value)

    def test_raises_when_guards_exceed_total(self):
        """Test raises ValueError when guards exceed total frames."""
        with pytest.raises(ValueError) as exc_info:
            _ = calculate_usable_frames(
                total_frames=100,
                guard_start_frames=60,
                guard_end_frames=60,
            )
        assert "No usable frames after guards" in str(exc_info.value)

    def test_raises_when_guards_equal_total(self):
        """Test raises ValueError when guards equal total frames."""
        with pytest.raises(ValueError) as exc_info:
            _ = calculate_usable_frames(
                total_frames=100,
                guard_start_frames=50,
                guard_end_frames=50,
            )
        assert "No usable frames after guards" in str(exc_info.value)

    def test_minimum_usable_frames(self):
        """Test with guards leaving exactly 1 usable frame."""
        result = calculate_usable_frames(
            total_frames=101,
            guard_start_frames=50,
            guard_end_frames=50,
        )
        assert result == 1


class TestBuildX265Command:
    """Tests for build_x265_command function."""

    def test_uses_y4m_stdin_input(self):
        """Test command reads y4m from stdin instead of a .vpy file."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X265)
        args = build_x265_command(
            paths=paths,
            output_path=Path("output.hevc"),
            encoder_params=["--crf", "18"],
            preset="medium",
            cwd=Path("C:/project"),
        )
        assert "--y4m" in args
        assert "--input" in args
        assert args[args.index("--input") + 1] == "-"

    def test_no_reader_options(self):
        """Test command does not include --reader-options (no direct VS loading)."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X265)
        args = build_x265_command(
            paths=paths,
            output_path=Path("output.hevc"),
            encoder_params=[],
            preset="medium",
            cwd=Path("C:/project"),
        )
        assert not any("reader-options" in a for a in args)

    def test_includes_preset_and_output(self):
        """Test command includes preset and output path."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X265)
        args = build_x265_command(
            paths=paths,
            output_path=Path("output.hevc"),
            encoder_params=["--crf", "20"],
            preset="slow",
            cwd=Path("C:/project"),
        )
        assert "--preset" in args
        assert "slow" in args
        assert "--output" in args

    def test_no_preset(self):
        """Test command omits --preset when preset is None."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X265)
        args = build_x265_command(
            paths=paths,
            output_path=Path("output.hevc"),
            encoder_params=[],
            preset=None,
            cwd=Path("C:/project"),
        )
        assert "--preset" not in args


class TestBuildX264Command:
    """Tests for build_x264_command function."""

    def test_uses_y4m_demuxer_with_positional_input(self):
        """Test x264 command uses --demuxer y4m and positional '-' input."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X264)
        args = build_x264_command(
            paths=paths,
            output_path=Path("output.264"),
            encoder_params=["--crf", "18"],
            preset="medium",
            cwd=Path("C:/project"),
        )
        assert "--demuxer" in args
        assert "y4m" in args
        assert args[-1] == "-"

    def test_includes_preset_and_output(self):
        """Test x264 command includes preset and output path."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X264)
        args = build_x264_command(
            paths=paths,
            output_path=Path("output.264"),
            encoder_params=["--crf", "20"],
            preset="slow",
            cwd=Path("C:/project"),
        )
        assert "--preset" in args
        assert "slow" in args
        assert "--output" in args

    def test_no_preset(self):
        """Test x264 command omits --preset when preset is None."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X264)
        args = build_x264_command(
            paths=paths,
            output_path=Path("output.264"),
            encoder_params=[],
            preset=None,
            cwd=Path("C:/project"),
        )
        assert "--preset" not in args


class TestBuildEncoderCommand:
    """Tests for build_encoder_command dispatcher."""

    def test_dispatches_to_x265(self):
        """Test dispatches to x265 command builder."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X265)
        args = build_encoder_command(
            paths=paths,
            output_path=Path("output.hevc"),
            encoder_params=["--crf", "18"],
            preset="medium",
            cwd=Path("C:/project"),
        )
        # x265 uses --y4m --input -
        assert "--y4m" in args
        assert "--input" in args

    def test_dispatches_to_x264(self):
        """Test dispatches to x264 command builder."""
        paths = EncoderPaths.from_cwd(Path("C:/project"), EncoderType.X264)
        args = build_encoder_command(
            paths=paths,
            output_path=Path("output.264"),
            encoder_params=["--crf", "18"],
            preset="medium",
            cwd=Path("C:/project"),
        )
        # x264 uses --demuxer y4m ... -
        assert "--demuxer" in args
        assert args[-1] == "-"


class TestBuildVspipeCommand:
    """Tests for build_vspipe_command function."""

    def test_basic_command(self):
        """Test basic vspipe command structure."""
        vs_env = VapourSynthEnv.from_cwd(Path("C:/project"))
        args = build_vspipe_command(
            vs_env=vs_env,
            vpy_path=Path("script.vpy"),
            cwd=Path("C:/project"),
        )
        assert args[0] == str(
            Path("C:/project/vapoursynth-portable") / VS_PACKAGE_SUBDIR / "vspipe.exe"
        )
        assert "-c" in args
        assert "y4m" in args
        assert args[-1] == "-"

    def test_resolves_vpy_path(self):
        """Test vpy path is resolved to absolute."""
        vs_env = VapourSynthEnv.from_cwd(Path("C:/project"))
        args = build_vspipe_command(
            vs_env=vs_env,
            vpy_path=Path("temp/script.vpy"),
            cwd=Path("C:/project"),
        )
        assert str(Path("C:/project/temp/script.vpy")) in args


def _sampled_source(name: str, start: int = 0, end: int = 10000) -> SampledSource:
    return SampledSource(
        path=Path(f"{name}.mkv"),
        cache_file=Path(f"{name}.ffindex"),
        usable_range=UsableRange(start=start, end=end, frame_count=end - start),
    )


class TestBuildSamplingVpyScriptOneSource:
    """The ordinary case: a job reading a single file."""

    def test_loads_the_source_and_samples_it(self) -> None:
        script = build_sampling_vpy_script(
            sources=[_sampled_source("input")],
            interval_frames=1600,
            region_frames=20,
            fps=24.0,
            indexer=IndexerType.FFMS2,
        )

        assert script.count("ffms2.Source") == 1
        assert "[0:10000]" in script
        assert "SelectEvery(1600" in script

    def test_a_single_source_is_not_spliced(self) -> None:
        script = build_sampling_vpy_script(
            sources=[_sampled_source("input")],
            interval_frames=1600,
            region_frames=20,
            fps=24.0,
            indexer=IndexerType.FFMS2,
        )

        assert "Splice" not in script


class TestBuildSamplingVpyScriptManySources:
    """Several files read as one source: sample each, then join the samples."""

    def test_every_source_is_loaded_and_sampled(self) -> None:
        script = build_sampling_vpy_script(
            sources=[
                _sampled_source("one", 100, 5000),
                _sampled_source("two", 200, 6000),
                _sampled_source("three", 0, 7000),
            ],
            interval_frames=1600,
            region_frames=20,
            fps=24.0,
            indexer=IndexerType.FFMS2,
        )

        assert script.count("ffms2.Source") == 3
        assert script.count("SelectEvery(1600") == 3, "each file samples itself"
        for expected in ("[100:5000]", "[200:6000]", "[0:7000]"):
            assert expected in script, "each file keeps its own guard bands"

    def test_the_samples_are_joined_once(self) -> None:
        script = build_sampling_vpy_script(
            sources=[_sampled_source("one"), _sampled_source("two")],
            interval_frames=1600,
            region_frames=20,
            fps=24.0,
            indexer=IndexerType.FFMS2,
        )

        assert script.count("std.Splice") == 1

    def test_crop_is_applied_once_after_joining(self) -> None:
        """One crop covers every file, so it belongs on the joined clip."""
        script = build_sampling_vpy_script(
            sources=[_sampled_source("one"), _sampled_source("two")],
            interval_frames=1600,
            region_frames=20,
            fps=24.0,
            crop_values=CropValues(left=0, right=0, top=140, bottom=140),
            indexer=IndexerType.FFMS2,
        )

        assert script.count("std.Crop") == 1
        assert script.index("std.Splice") < script.index("std.Crop")


def test_each_indexer_has_its_own_index_suffix() -> None:
    # Different suffixes mean switching indexers cannot collide: an index of
    # the other kind stays on disk unused rather than being read as the wrong
    # format.
    suffixes = {indexer_spec(i).index_suffix for i in IndexerType}
    assert suffixes == {".ffindex", ".lwi"}


def test_ffms2_spec_names_its_own_filter_and_tools() -> None:
    spec = indexer_spec(IndexerType.FFMS2)
    assert spec.index_suffix == ".ffindex"
    assert spec.indexer_exe == "ffmsindex.exe"
    assert spec.plugin_dll == "ffms2.dll"
    assert spec.display_name == "FFMS2"


def test_lsmash_spec_names_its_own_filter_and_tools() -> None:
    spec = indexer_spec(IndexerType.LSMASH)
    assert spec.index_suffix == ".lwi"
    assert spec.indexer_exe == "LSMASHSource_indexing.exe"
    assert spec.plugin_dll == "LSMASHSource.dll"
    assert spec.display_name == "LSMASHSource"


def test_ffms2_source_expression_is_positional() -> None:
    expr = indexer_spec(IndexerType.FFMS2).source_expr("C:/in.mkv", "C:/in.ffindex")
    assert expr == 'core.ffms2.Source(r"C:/in.mkv", cachefile=r"C:/in.ffindex")'


def test_lsmash_source_expression_names_its_source_argument() -> None:
    expr = indexer_spec(IndexerType.LSMASH).source_expr("C:/in.mkv", "C:/in.lwi")
    expected = 'core.lsmas.LWLibavSource(source=r"C:/in.mkv", cachefile=r"C:/in.lwi")'
    assert expr == expected


def test_sampling_script_uses_the_selected_filter(tmp_path: Path) -> None:
    source = SampledSource(
        path=tmp_path / "in.mkv",
        cache_file=tmp_path / "in.lwi",
        usable_range=UsableRange(start=0, end=100, frame_count=100),
    )
    script = build_sampling_vpy_script(
        [source],
        interval_frames=10,
        region_frames=2,
        fps=24.0,
        indexer=IndexerType.LSMASH,
    )
    assert "core.lsmas.LWLibavSource(" in script
    assert "core.ffms2.Source(" not in script


REPO_ROOT = Path(__file__).resolve().parents[1]
VS_PYTHON = REPO_ROOT / "vapoursynth-portable" / "python.exe"

requires_vapoursynth = pytest.mark.skipif(
    not VS_PYTHON.exists(),
    reason="vapoursynth-portable is not provisioned; run tools/update_vs.py",
)


class TestReadIndexedFrameCount:
    """The true count, read from the index the run already built.

    `estimate_frame_count` multiplies a rate by a duration, which no single
    rate describes for a variable frame rate file. The indexed clip's own
    length is what the sampler will actually see, so it is the right answer
    rather than a closer guess.
    """

    @requires_vapoursynth
    def test_reads_the_count_the_sampler_will_see(self, tmp_path: Path) -> None:
        source = tmp_path / "clip.mkv"
        _ = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x64:rate=24:duration=2",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx265",
                "-x265-params",
                "lossless=1",
                str(source),
            ],
            capture_output=True,
            check=True,
        )

        count = read_indexed_frame_count(
            source,
            tmp_path / "clip.ffindex",
            indexer=IndexerType.FFMS2,
            cwd=REPO_ROOT,
        )
        assert count == 48  # 2 seconds at 24fps

    @requires_vapoursynth
    def test_a_variable_rate_clip_reports_its_real_length(self, tmp_path: Path) -> None:
        # The case the estimate gets wrong: dropping two frames in three and
        # stretching the timestamps leaves a clip whose nominal rate times its
        # duration overshoots what it actually holds.
        source = tmp_path / "vfr.mkv"
        _ = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x64:rate=24:duration=6",
                "-vf",
                "select='not(mod(n,3))',setpts=N/(24*TB)*1.37",
                "-fps_mode",
                "vfr",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx265",
                "-x265-params",
                "lossless=1",
                str(source),
            ],
            capture_output=True,
            check=True,
        )

        count = read_indexed_frame_count(
            source,
            tmp_path / "vfr.ffindex",
            indexer=IndexerType.FFMS2,
            cwd=REPO_ROOT,
        )
        assert count == 48  # 144 source frames, every third kept

    def test_an_unreadable_source_yields_nothing_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        # A frame count must never be able to end a run: the caller keeps its
        # estimate and warns.
        count = read_indexed_frame_count(
            tmp_path / "does-not-exist.mkv",
            tmp_path / "does-not-exist.ffindex",
            indexer=IndexerType.FFMS2,
            cwd=REPO_ROOT,
        )
        assert count is None


class TestDistortedIndexPath:
    """One index per distorted encode, shared by whichever metrics read it."""

    def test_both_metrics_derive_the_same_path_for_one_file(self) -> None:
        # In shared-sampling mode VMAF and SSIMULACRA2 score the same encode.
        # Two names for its index means indexing it twice, and a search of ten
        # iterations pays that ten times over.
        dis = Path("C:/job/profile_a/shared_distorted_crf28.0_iter1.mkv")
        first = distorted_index_path(dis, IndexerType.FFMS2)
        second = distorted_index_path(dis, IndexerType.FFMS2)
        assert first == second

    def test_the_same_name_in_two_profiles_does_not_collide(self) -> None:
        # Multi-profile mode encodes one file per profile, and they share a
        # stem. A shared index path would hand one profile's index to another
        # profile's encode; ffms2 rejects a mismatched index outright rather
        # than rebuilding, and that failure is swallowed into a dropped metric.
        a = Path("C:/job/profile_a/shared_distorted_crf28.0_iter1.mkv")
        b = Path("C:/job/profile_b/shared_distorted_crf28.0_iter1.mkv")
        assert distorted_index_path(a, IndexerType.FFMS2) != distorted_index_path(
            b, IndexerType.FFMS2
        )

    def test_the_index_sits_beside_the_encode_it_describes(self) -> None:
        dis = Path("C:/job/profile_a/shared_distorted_crf28.0_iter1.mkv")
        path = distorted_index_path(dis, IndexerType.FFMS2)
        assert path.parent == dis.parent
        assert path.name.startswith(dis.stem)

    def test_each_indexer_names_its_own_suffix(self) -> None:
        # Switching indexers must not hand one filter the other's index.
        dis = Path("C:/job/dis.mkv")
        ffms2 = distorted_index_path(dis, IndexerType.FFMS2)
        lsmash = distorted_index_path(dis, IndexerType.LSMASH)
        assert ffms2 != lsmash
        assert ffms2.suffix == indexer_spec(IndexerType.FFMS2).index_suffix
        assert lsmash.suffix == indexer_spec(IndexerType.LSMASH).index_suffix


class TestRunVspipeEncodeStop:
    """The optional early-stop event."""

    @staticmethod
    def _producer(count: int) -> list[str]:
        return [
            sys.executable,
            "-c",
            f"import sys\nfor i in range({count}):\n"
            "    sys.stdout.write(f'{i}\\n')\n    sys.stdout.flush()\n",
        ]

    @staticmethod
    def _echo() -> list[str]:
        return [
            sys.executable,
            "-c",
            "import sys\nfor line in sys.stdin:\n"
            "    sys.stderr.write(line)\n    sys.stderr.flush()\n",
        ]

    def test_a_set_event_returns_instead_of_raising(self) -> None:
        # Both processes are killed on purpose, so their exit codes say
        # nothing. Treating them as failures would turn every early stop
        # into a spurious error.
        stop = threading.Event()

        def handler(line: str) -> bool:
            if line.strip() == "5":
                stop.set()
            return True

        out = run_vspipe_encode(
            vspipe_args=self._producer(200_000),
            encoder_args=self._echo(),
            vs_env=dict(os.environ),
            cwd=None,
            line_handler=handler,
            stop=stop,
        )
        assert "5" in out

    def test_a_set_event_stops_before_the_end(self) -> None:
        stop = threading.Event()

        def handler(line: str) -> bool:
            if line.strip() == "5":
                stop.set()
            return True

        out = run_vspipe_encode(
            vspipe_args=self._producer(200_000),
            encoder_args=self._echo(),
            vs_env=dict(os.environ),
            cwd=None,
            line_handler=handler,
            stop=stop,
        )
        # Generous: pipe buffers let a lot through before the kill lands.
        # The claim is only that it did not run to completion.
        assert len(out.splitlines()) < 200_000

    def test_no_event_behaves_exactly_as_before(self) -> None:
        out = run_vspipe_encode(
            vspipe_args=self._producer(50),
            encoder_args=self._echo(),
            vs_env=dict(os.environ),
            cwd=None,
            line_handler=None,
        )
        assert len(out.splitlines()) == 50

    def test_an_unset_event_does_not_stop_anything(self) -> None:
        stop = threading.Event()
        out = run_vspipe_encode(
            vspipe_args=self._producer(50),
            encoder_args=self._echo(),
            vs_env=dict(os.environ),
            cwd=None,
            line_handler=None,
            stop=stop,
        )
        assert len(out.splitlines()) == 50


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


class TestCombineCropValues:
    """Reconciling crops when files in one source disagree on active area.

    Splicing needs every clip the same size, so one crop must serve them all.
    The safest crop wins on each edge independently: the smallest, matching
    how frames combine within a single file.
    """

    def test_single_value_is_returned_unchanged(self) -> None:
        crop = CropValues(left=0, right=0, top=140, bottom=140)
        assert combine_crop_values([crop]) == crop

    def test_the_safest_edge_wins(self) -> None:
        a = CropValues(left=0, right=0, top=140, bottom=140)
        b = CropValues(left=0, right=0, top=132, bottom=132)

        assert combine_crop_values([a, b]) == b

    def test_a_file_that_reaches_the_edge_protects_every_file(self) -> None:
        # The case this rule exists for. A source whose aspect ratio changes
        # part way through - a film with taller sequences - has bars in one
        # file and none in another. Taking the larger crop would cut the
        # taller footage; taking the smaller leaves bars on the rest, which
        # is recoverable.
        letterboxed = CropValues(left=0, right=0, top=132, bottom=132)
        full_height = CropValues(left=0, right=0, top=0, bottom=0)

        assert combine_crop_values([letterboxed, full_height]) == full_height

    def test_it_matches_combining_every_frame_at_once(self) -> None:
        # Taking the smallest is associative, so measuring per file and then
        # combining gives the same answer as one pass over every frame of
        # every file. That equivalence is the whole point: it makes the
        # documented rule - the smallest crop across all sampled frames -
        # true for a directory of files as well as for one.
        per_file = [
            CropValues(left=8, right=0, top=140, bottom=8),
            CropValues(left=0, right=12, top=6, bottom=136),
            CropValues(left=4, right=20, top=20, bottom=40),
        ]
        every_frame_at_once = CropValues(
            left=min(v.left for v in per_file),
            right=min(v.right for v in per_file),
            top=min(v.top for v in per_file),
            bottom=min(v.bottom for v in per_file),
        )

        assert combine_crop_values(per_file) == every_frame_at_once

    def test_edges_are_combined_independently(self) -> None:
        """The result can be a crop no single file measured."""
        a = CropValues(left=8, right=0, top=140, bottom=0)
        b = CropValues(left=0, right=12, top=0, bottom=136)

        combined = combine_crop_values([a, b])

        assert combined == CropValues(left=0, right=0, top=0, bottom=0)
        assert combined not in (a, b)

    def test_no_values_means_no_crop(self) -> None:
        assert combine_crop_values([]) == CropValues(left=0, right=0, top=0, bottom=0)
