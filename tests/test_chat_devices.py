"""Offline coverage for conversational Spotify connection and device selection."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from tunetrail.api.app import Services, create_app
from tunetrail.config import Settings
from tunetrail.domain.models import ChatInterpretation, Device, SpotifyChatAction
from tunetrail.spotify.client import SpotifyClient


class Parser:
    async def parse(self, request: str) -> ChatInterpretation:
        actions = {
            "ok lets check the active devices": ("list_devices", None),
            "make Desk active": ("select_device", "Desk"),
            "yes please": ("confirm", None),
            "no thanks": ("cancel", None),
            "how is my Spotify connection?": ("connection_status", None),
            "please connect my Spotify": ("connect", None),
        }
        action, name = actions[request]
        return ChatInterpretation(spotify_action=SpotifyChatAction(action=action, device_name=name))  # type: ignore[arg-type]


def test_spotify_action_cannot_be_combined_with_other_work() -> None:
    with pytest.raises(ValidationError):
        ChatInterpretation(
            spotify_action=SpotifyChatAction(action="confirm"), clarification="Not sure"
        )


@pytest.mark.asyncio
async def test_device_selection_requires_separate_confirmation(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    services.parser = Parser()
    services.spotify.devices = AsyncMock(
        return_value=[Device(id="secret-id", name="Desk", kind="Computer")]
    )
    services.spotify.select_device = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]

        async def chat(message: str) -> httpx.Response:
            return await client.post(
                "/api/chat", headers={"X-TuneTrail-Token": token}, json={"message": message}
            )

        listing = (await chat("ok lets check the active devices")).json()
        assert listing["devices"][0]["name"] == "Desk"
        assert "secret-id" not in str(listing)
        assert (await chat("how is my Spotify connection?")).json()["spotify_connection"][
            "status"
        ] == "idle"
        assert "Confirm or cancel" in (await chat("make Desk active")).json()["device_message"]
        services.spotify.select_device.assert_not_awaited()
        assert (await chat("yes please")).json()["device_transfer_requested"]
        services.spotify.select_device.assert_awaited_once()
        assert (await chat("yes please")).status_code == 409
        await chat("make Desk active")
        assert "cancelled" in (await chat("no thanks")).json()["device_message"]
        assert (await chat("yes please")).status_code == 409
        await chat("make Desk active")
        services.device_confirmation = ("secret-id", "Desk", 0)
        assert (await chat("yes please")).status_code == 409
        assert services.spotify.select_device.await_count == 1
    await services.http.aclose()


@pytest.mark.asyncio
async def test_natural_connection_confirmation_and_cancel_preserve_credentials(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "auth"
    credentials.write_text("original")
    services = Services(Settings(spotify_client_id="client"), tmp_path / "db", credentials)
    services.parser = Parser()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]

        async def chat(message: str) -> httpx.Response:
            return await client.post(
                "/api/chat", headers={"X-TuneTrail-Token": token}, json={"message": message}
            )

        assert (await chat("please connect my Spotify")).json()["spotify_connection"][
            "status"
        ] == "confirmation_required"
        await chat("no thanks")
        assert (await chat("yes please")).status_code == 409
        await chat("please connect my Spotify")
        credentials.write_text("changed by owner")
        assert (await chat("yes please")).status_code == 409
        assert credentials.read_text() == "changed by owner"
        assert services.connect_task is None
    await services.http.aclose()


@pytest.mark.asyncio
async def test_device_duplicates_restriction_and_change_fail_closed(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    services.parser = Parser()
    device = Device(id="a", name="Desk", kind="Computer")
    services.spotify.devices = AsyncMock(return_value=[device, device])
    services.spotify.select_device = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]

        async def chat(message: str) -> httpx.Response:
            return await client.post(
                "/api/chat", headers={"X-TuneTrail-Token": token}, json={"message": message}
            )

        assert "ambiguous" in (await chat("make Desk active")).json()["clarification"]
        services.spotify.devices.return_value = [device.model_copy(update={"is_restricted": True})]
        assert "cannot" in (await chat("make Desk active")).json()["clarification"]
        services.spotify.devices.return_value = [device]
        await chat("make Desk active")
        services.spotify.devices.return_value = []
        assert (await chat("yes please")).status_code == 409
        services.spotify.select_device.assert_not_awaited()
    await services.http.aclose()


@pytest.mark.asyncio
async def test_confirm_sends_one_exact_approved_transfer(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    services.parser = Parser()
    calls: list[tuple[str, str, bytes]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/devices"):
            return httpx.Response(
                200,
                json={
                    "devices": [
                        {
                            "id": "a",
                            "name": "Desk",
                            "type": "Computer",
                            "is_active": False,
                            "is_restricted": False,
                            "volume_percent": 50,
                        }
                    ]
                },
            )
        return httpx.Response(204)

    await services.http.aclose()
    services.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    services.auth.access_token = AsyncMock(return_value="private")
    services.spotify = SpotifyClient(services.auth, services.http)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        headers = {"X-TuneTrail-Token": token}
        await client.post("/api/chat", headers=headers, json={"message": "make Desk active"})
        assert not [call for call in calls if call[0] == "PUT"]
        response = await client.post("/api/chat", headers=headers, json={"message": "yes please"})
        assert response.status_code == 200
        mutations = [call for call in calls if call[0] == "PUT"]
        assert len(mutations) == 1
        assert mutations[0][1] == "/v1/me/player"
        assert json.loads(mutations[0][2]) == {"device_ids": ["a"], "play": False}
    await services.http.aclose()
