"""Specific-song chat playback with mocked Spotify requests only."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from tests.test_spotify_models import track_payload
from tunetrail.api.app import Services, create_app
from tunetrail.auth.models import OAuthToken
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings


@pytest.mark.asyncio
@respx.mock
async def test_song_search_selection_and_exact_single_use_playback(tmp_path: Path) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    first = {**track_payload(), "name": "You Belong With Me", "is_playable": True}
    second = {
        **first,
        "id": "other",
        "uri": "spotify:track:other",
        "name": "You Belong With Me (Version)",
    }
    unavailable = {**first, "is_playable": False}
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={"tracks": {"items": [first, second, unavailable], "total": 100, "next": "next"}},
        )
    )
    respx.get("https://api.example/v1/me/player/devices").mock(
        return_value=httpx.Response(
            200,
            json={
                "devices": [
                    {
                        "id": "device1",
                        "name": "Desk",
                        "type": "Computer",
                        "is_active": True,
                        "is_restricted": False,
                    }
                ]
            },
        )
    )
    play = respx.put("https://api.example/v1/me/player/play", params={"device_id": "device1"}).mock(
        return_value=httpx.Response(204)
    )
    respx.get("https://api.example/v1/me/player").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]

        async def chat(message: str) -> httpx.Response:
            return await client.post(
                "/api/chat", headers={"x-tunetrail-token": token}, json={"message": message}
            )

        listing = (await chat("play you belong with me")).json()["song_message"]
        assert "1. You Belong With Me" in listing and "2. You Belong With Me (Version)" in listing
        assert "3." not in listing and "spotify:track" not in listing
        assert (await chat("confirm play")).status_code == 409
        await chat("play you belong with me")
        assert "Choose a number" in (await chat("select track 9")).json()["song_message"]
        assert "Version" in (await chat("select track 2")).json()["song_message"]
        assert not play.called
        assert (await chat("confirm play")).json()["song_playback_requested"]
        assert play.call_count == 1
        assert play.calls.last.request.content == b'{"uris":["spotify:track:other"]}'
        assert (await chat("confirm play")).status_code == 409
        assert play.call_count == 1
    await services.http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_song_cancel_expiry_and_no_active_device(tmp_path: Path) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    track = {**track_payload(), "is_playable": True}
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track]}})
    )
    device_route = respx.get("https://api.example/v1/me/player/devices").mock(
        return_value=httpx.Response(200, json={"devices": []})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]

        async def chat(message: str) -> httpx.Response:
            return await client.post(
                "/api/chat", headers={"x-tunetrail-token": token}, json={"message": message}
            )

        assert "confirm play" in (await chat("play Some Track")).json()["song_message"]
        await chat("cancel song")
        assert (await chat("confirm play")).status_code == 409
        await chat("play Some Track")
        assert services.song_confirmation is not None
        services.song_confirmation = (services.song_confirmation[0], 0)
        assert (await chat("confirm play")).status_code == 409
        await chat("play Some Track")
        assert "active Spotify device" in (await chat("confirm play")).json()["detail"]
        assert (await chat("confirm play")).status_code == 409
        device_route.mock(
            return_value=httpx.Response(
                200,
                json={
                    "devices": [
                        {
                            "id": "device1",
                            "name": "Desk",
                            "type": "Computer",
                            "is_active": True,
                            "is_restricted": False,
                        }
                    ]
                },
            )
        )
        play = respx.put("https://api.example/v1/me/player/play").mock(
            return_value=httpx.Response(403)
        )
        await chat("play Some Track")
        assert "may have succeeded" in (await chat("confirm play")).json()["detail"]
        assert (await chat("confirm play")).status_code == 409
        assert play.call_count == 1
    await services.http.aclose()
