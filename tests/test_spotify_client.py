"""Mocked tests for Spotify Web API calls, retries, and errors."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from tests.test_spotify_models import track_payload
from tunetrail.auth.models import OAuthToken
from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.auth.store import FileCredentialStore
from tunetrail.spotify.client import SpotifyClient
from tunetrail.spotify.errors import SpotifyNoActiveDeviceError, SpotifyRateLimitError


async def spotify_client(
    tmp_path: Path, client: httpx.AsyncClient, sleeps: list[float]
) -> SpotifyClient:
    """Build a Spotify client with a durable mocked local OAuth session."""
    store = FileCredentialStore(tmp_path / "auth.json")
    await store.save_token(
        OAuthToken(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    authenticator = SpotifyAuthenticator(
        client_id="client-id",
        redirect_uri="http://127.0.0.1:8765/callback",
        scopes="user-read-private",
        credentials=store,
        http_client=client,
        accounts_url="https://accounts.example",
    )
    return SpotifyClient(
        authenticator,
        client,
        api_url="https://api.example/v1",
        sleep=sleep,
    )


@respx.mock
async def test_client_reads_user_devices_and_search_results(tmp_path: Path) -> None:
    """Typed client maps common non-mutating Spotify API responses."""
    respx.get("https://api.example/v1/me").mock(return_value=httpx.Response(200, json={"id": "me"}))
    respx.get("https://api.example/v1/me/player/devices").mock(
        return_value=httpx.Response(
            200,
            json={
                "devices": [
                    {
                        "id": "device-1",
                        "name": "Desktop",
                        "type": "Computer",
                        "is_active": True,
                        "is_restricted": False,
                        "volume_percent": 40,
                    }
                ]
            },
        )
    )
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track_payload()]}})
    )
    async with httpx.AsyncClient() as http_client:
        client = await spotify_client(tmp_path, http_client, [])
        user = await client.current_user()
        devices = await client.devices()
        tracks = await client.search_tracks("track")

    assert user.id == "me"
    assert devices[0].is_active
    assert tracks[0].title == "Track"


@respx.mock
async def test_safe_get_retries_spotify_rate_limits(tmp_path: Path) -> None:
    """A GET retries after Spotify's Retry-After response."""
    route = respx.get("https://api.example/v1/me").mock(
        side_effect=[
            httpx.Response(
                429, headers={"Retry-After": "2"}, json={"error": {"message": "slow down"}}
            ),
            httpx.Response(200, json={"id": "me"}),
        ]
    )
    sleeps: list[float] = []
    async with httpx.AsyncClient() as http_client:
        client = await spotify_client(tmp_path, http_client, sleeps)
        user = await client.current_user()

    assert user.id == "me"
    assert route.call_count == 2
    assert sleeps == [2.0]


@respx.mock
async def test_player_mutation_refreshes_playback(tmp_path: Path) -> None:
    """Player commands are followed by a fresh now-playing state request."""
    pause = respx.put("https://api.example/v1/me/player/pause").mock(
        return_value=httpx.Response(204)
    )
    respx.get("https://api.example/v1/me/player").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http_client:
        client = await spotify_client(tmp_path, http_client, [])
        await client.pause(device_id="device-1")

    assert pause.called
    assert client.last_playback is None


@respx.mock
async def test_rate_limit_on_mutation_is_reported_without_retry(tmp_path: Path) -> None:
    """Unsafe player mutations are not retried automatically after a 429."""
    respx.put("https://api.example/v1/me/player/pause").mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "3"}, json={"error": {"message": "slow down"}}
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = await spotify_client(tmp_path, http_client, [])
        with pytest.raises(SpotifyRateLimitError) as error:
            await client.pause()

    assert error.value.retry_after_seconds == 3.0


@respx.mock
async def test_no_active_device_has_a_specific_error(tmp_path: Path) -> None:
    """Player failures without a device produce an actionable error type."""
    respx.put("https://api.example/v1/me/player/pause").mock(
        return_value=httpx.Response(404, json={"error": {"message": "No active device found"}})
    )
    async with httpx.AsyncClient() as http_client:
        client = await spotify_client(tmp_path, http_client, [])
        with pytest.raises(SpotifyNoActiveDeviceError):
            await client.pause()
