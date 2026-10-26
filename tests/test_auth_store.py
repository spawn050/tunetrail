"""Tests for local Spotify credential storage."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tunetrail.auth.models import OAuthToken
from tunetrail.auth.store import FileCredentialStore


def token() -> OAuthToken:
    """Return a valid OAuth token fixture."""
    return OAuthToken(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def test_file_store_round_trips_an_oauth_token(tmp_path: Path) -> None:
    """Tokens are persisted and loaded from the configured private file."""
    store = FileCredentialStore(tmp_path / "auth.json")
    expected = token()

    await store.save_token(expected)

    assert await store.load_token() == expected


async def test_file_store_clear_removes_token_file(tmp_path: Path) -> None:
    """Logout can remove all persisted Spotify token material."""
    path = tmp_path / "auth.json"
    store = FileCredentialStore(path)
    await store.save_token(token())

    await store.clear()

    assert not path.exists()
