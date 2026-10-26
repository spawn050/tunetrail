"""Tests for application configuration and local paths."""

import stat
from pathlib import Path

from tunetrail.config import Settings
from tunetrail.paths import AppPaths


def test_settings_loads_file_then_environment(tmp_path: Path) -> None:
    """Environment variables take precedence over local TOML configuration."""
    paths = AppPaths(tmp_path / "config", tmp_path / "state", tmp_path / "cache", tmp_path / "data")
    paths.config_dir.mkdir()
    paths.settings_file.write_text('llm_model = "from-file"\nllm_temperature = 0.3\n')

    settings = Settings.load(paths, {"TUNETRAIL_LLM_MODEL": "from-environment"})

    assert settings.llm_model == "from-environment"
    assert settings.llm_temperature == 0.3


def test_default_paths_honor_xdg_locations() -> None:
    """All default directories are isolated under their XDG homes."""
    paths = AppPaths.default(
        {
            "HOME": "/home/example",
            "XDG_CONFIG_HOME": "/settings",
            "XDG_STATE_HOME": "/state",
            "XDG_CACHE_HOME": "/cache",
            "XDG_DATA_HOME": "/data",
        }
    )

    assert paths.config_dir == Path("/settings/tunetrail")
    assert paths.auth_file == Path("/settings/tunetrail/auth.json")
    assert paths.database_file == Path("/data/tunetrail/tunetrail.sqlite3")


def test_create_directories_makes_private_application_directories(tmp_path: Path) -> None:
    """Local application directories are owner-only."""
    paths = AppPaths(tmp_path / "config", tmp_path / "state", tmp_path / "cache", tmp_path / "data")

    paths.create_directories()

    assert all(
        directory.is_dir()
        for directory in (paths.config_dir, paths.state_dir, paths.cache_dir, paths.data_dir)
    )
    assert stat.S_IMODE(paths.config_dir.stat().st_mode) == 0o700
