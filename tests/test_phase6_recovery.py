"""Offline recovery messages must be useful without exposing provider errors."""

from pathlib import Path

import httpx
import pytest

from tunetrail.api.app import Services, create_app
from tunetrail.auth.service import SpotifyNotAuthenticatedError
from tunetrail.config import Settings
from tunetrail.spotify.errors import (
    SpotifyNoActiveDeviceError,
    SpotifyPermissionError,
    SpotifyRateLimitError,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SpotifyNotAuthenticatedError("secret"), "authentication"),
        (SpotifyNoActiveDeviceError(404, "secret"), "active Spotify device"),
        (SpotifyPermissionError(403, "secret"), "scopes"),
        (SpotifyRateLimitError("secret", 10), "rate limit"),
        (httpx.ConnectError("secret"), "network"),
    ],
)
async def test_safe_recovery_messages(tmp_path: Path, error: Exception, expected: str) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")

    async def fail() -> list[object]:
        raise error

    services.spotify.devices = fail  # type: ignore[method-assign]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        response = await client.get("/api/devices")
        assert response.status_code == 502
        assert expected in response.json()["detail"]
        assert "secret" not in response.text
    await services.http.aclose()
