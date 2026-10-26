"""Standard local filesystem locations used by TuneTrail."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Application-owned local directories and files."""

    config_dir: Path
    state_dir: Path
    cache_dir: Path
    data_dir: Path

    @property
    def settings_file(self) -> Path:
        """Return the optional local settings file."""
        return self.config_dir / "config.toml"

    @property
    def auth_file(self) -> Path:
        """Return the default file for Spotify token material."""
        return self.config_dir / "auth.json"

    @property
    def database_file(self) -> Path:
        """Return the SQLite database location."""
        return self.data_dir / "tunetrail.sqlite3"

    @classmethod
    def default(cls, environ: dict[str, str] | None = None) -> "AppPaths":
        """Build XDG-aware paths, allowing explicit environment overrides."""
        env = os.environ if environ is None else environ
        home = Path(env.get("HOME", str(Path.home())))
        config_home = Path(env.get("XDG_CONFIG_HOME", home / ".config"))
        state_home = Path(env.get("XDG_STATE_HOME", home / ".local" / "state"))
        cache_home = Path(env.get("XDG_CACHE_HOME", home / ".cache"))
        data_home = Path(env.get("XDG_DATA_HOME", home / ".local" / "share"))

        return cls(
            config_dir=Path(env.get("TUNETRAIL_CONFIG_DIR", config_home / "tunetrail")),
            state_dir=Path(env.get("TUNETRAIL_STATE_DIR", state_home / "tunetrail")),
            cache_dir=Path(env.get("TUNETRAIL_CACHE_DIR", cache_home / "tunetrail")),
            data_dir=Path(env.get("TUNETRAIL_DATA_DIR", data_home / "tunetrail")),
        )

    def create_directories(self) -> None:
        """Create application directories with owner-only permissions."""
        for directory in (self.config_dir, self.state_dir, self.cache_dir, self.data_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
