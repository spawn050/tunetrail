"""Typed configuration from a local TOML file and environment variables."""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tunetrail.paths import AppPaths


class Settings(BaseModel):
    """Runtime settings that do not include OAuth token material."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spotify_client_id: str | None = None
    spotify_redirect_uri: str = "http://127.0.0.1:8765/callback"
    llm_provider: Literal["openrouter", "anthropic", "openai"] = "openrouter"
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    request_timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def load(
        cls,
        paths: AppPaths | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "Settings":
        """Load file values first, then override them with `TUNETRAIL_*` values."""
        resolved_paths = paths or AppPaths.default()
        env = os.environ if environ is None else environ
        values = _read_toml(resolved_paths.settings_file)
        for field_name in cls.model_fields:
            env_name = f"TUNETRAIL_{field_name.upper()}"
            if value := env.get(env_name):
                values[field_name] = value
        return cls.model_validate(values)


def _read_toml(path: Path) -> dict[str, object]:
    """Return a flat settings mapping from an optional TOML configuration file."""
    if not path.exists():
        return {}
    with path.open("rb") as config_file:
        values = tomllib.load(config_file)
    return dict(values)
