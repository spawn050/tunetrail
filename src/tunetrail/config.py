"""Typed configuration from a project-local `.env` file and environment variables."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Runtime settings that do not include OAuth token material."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spotify_client_id: str | None = None
    spotify_redirect_uri: str = "http://127.0.0.1:8765/callback"
    spotify_accounts_url: str = "https://accounts.spotify.com"
    spotify_api_url: str = "https://api.spotify.com/v1"
    spotify_scopes: str = (
        "user-read-private user-read-playback-state user-read-currently-playing "
        "user-modify-playback-state user-top-read user-read-recently-played "
        "playlist-read-private playlist-read-collaborative"
    )
    llm_provider: Literal["openrouter", "anthropic", "openai"] = "openrouter"
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_allow_data_collection: bool = False
    openrouter_api_key: str | None = None
    lastfm_api_key: str | None = None
    lastfm_username: str | None = None
    lastfm_api_url: str = "https://ws.audioscrobbler.com/2.0/"
    request_timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def load(
        cls,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> "Settings":
        """Load project `.env` values, overridden by real `TUNETRAIL_*` variables."""
        env = os.environ if environ is None else environ
        dotenv_path = env_file or Path.cwd() / ".env"
        values = _settings_values(dotenv_values(dotenv_path))
        values.update(_settings_values(env))
        return cls.model_validate(values)


def _settings_values(environ: Mapping[str, str | None]) -> dict[str, str]:
    """Map only supported non-empty environment names to settings fields."""
    values: dict[str, str] = {}
    for field_name in Settings.model_fields:
        value = environ.get(f"TUNETRAIL_{field_name.upper()}")
        if value:
            values[field_name] = value
    return values
