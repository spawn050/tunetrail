"""Owner-only file storage for Spotify OAuth tokens."""

import json
from pathlib import Path

from pydantic import ValidationError

from tunetrail.auth.models import OAuthToken
from tunetrail.security import assert_private_file, write_private_text


class CredentialFormatError(ValueError):
    """Raised when a local credential file cannot be parsed as an OAuth token."""


class FileCredentialStore:
    """Store Spotify token material in an owner-only JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load_token(self) -> OAuthToken | None:
        """Load and validate the saved token after checking its permissions."""
        if not self._path.exists():
            return None
        assert_private_file(self._path)
        try:
            contents = json.loads(self._path.read_text(encoding="utf-8"))
            return OAuthToken.model_validate(contents)
        except (json.JSONDecodeError, ValidationError) as error:
            raise CredentialFormatError(f"Could not read credentials from {self._path}") from error

    async def save_token(self, token: OAuthToken) -> None:
        """Persist an OAuth token with owner-only access."""
        write_private_text(self._path, token.model_dump_json(indent=2))

    async def clear(self) -> None:
        """Remove locally persisted Spotify token material, if present."""
        self._path.unlink(missing_ok=True)
