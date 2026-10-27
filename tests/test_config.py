"""Tests for application configuration and local paths."""

import stat
from pathlib import Path

import pytest

from tunetrail.config import Settings
from tunetrail.paths import AppPaths


def test_settings_loads_dotenv_then_environment(tmp_path: Path) -> None:
    """Real environment variables take precedence over project `.env` values."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TUNETRAIL_SPOTIFY_CLIENT_ID=from-dotenv\n"
        "TUNETRAIL_LLM_MODEL=from-dotenv\n"
        "TUNETRAIL_LLM_TEMPERATURE=0.3\n",
        encoding="utf-8",
    )

    settings = Settings.load({"TUNETRAIL_LLM_MODEL": "from-environment"}, env_file=env_file)

    assert settings.spotify_client_id == "from-dotenv"
    assert settings.llm_model == "from-environment"
    assert settings.llm_temperature == 0.3


def test_settings_automatically_loads_project_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup finds `.env` in the current project directory without extra flags."""
    (tmp_path / ".env").write_text(
        "TUNETRAIL_SPOTIFY_CLIENT_ID=from-project-dotenv\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings.load({})

    assert settings.spotify_client_id == "from-project-dotenv"


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
