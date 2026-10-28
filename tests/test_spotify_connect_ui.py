"""Offline tests for the local Spotify connection flow."""

import asyncio
import socket
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tunetrail.api.app import Services, create_app
from tunetrail.config import Settings


@pytest.mark.parametrize("existing,change", [(False, False), (True, False), (True, True)])
async def test_connect_and_preserve_credentials(
    tmp_path: Path, existing: bool, change: bool
) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = tmp_path / "auth"
    if existing:
        path.write_text("original")
    services = Services(
        Settings(
            spotify_client_id="client", spotify_redirect_uri=f"http://127.0.0.1:{port}/callback"
        ),
        tmp_path / "db",
        path,
    )
    await services.http.aclose()
    services.auth._http_client = httpx.AsyncClient(  # type: ignore[reportPrivateUsage]
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "expires_in": 3600,
                },
            )
        )
    )
    async with services.auth._http_client:  # type: ignore[reportPrivateUsage]  # noqa: SIM117
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(services)),
            base_url="http://127.0.0.1:8766",
        ) as client:
            token = (await client.get("/api/session")).json()["token"]
            headers = {"X-TuneTrail-Token": token}
            if existing:
                denied = await client.post("/api/spotify/connect", json={}, headers=headers)
                assert denied.status_code == 409
                assert path.read_text() == "original"
            response = await client.post(
                "/api/spotify/connect", json={"replace_existing": existing}, headers=headers
            )
            assert response.status_code == 200
            url = cast(str, response.json()["authorization_url"])
            state = parse_qs(urlsplit(url).query)["state"][0]
            if change:
                path.write_text("changed by owner")
            async with httpx.AsyncClient() as browser:
                await browser.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={"code": "test-code", "state": state},
                )
            assert services.connect_task is not None
            await asyncio.wait_for(services.connect_task, 2)
            status = (await client.get("/api/spotify/connect")).json()
            assert status["status"] == ("failed" if change else "connected")
            assert "private-access" not in str(status)
            if change:
                assert path.read_text() == "changed by owner"
            else:
                assert path.stat().st_mode & 0o777 == 0o600
                assert "private-access" in path.read_text()


async def test_missing_configuration_and_session_protection(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        assert (await client.post("/api/spotify/connect", json={})).status_code == 403
        token = (await client.get("/api/session")).json()["token"]
        response = await client.post(
            "/api/spotify/connect", json={}, headers={"X-TuneTrail-Token": token}
        )
        assert response.status_code == 400
        assert "CLIENT_ID" in response.text
    await services.http.aclose()


@pytest.mark.parametrize("cancel", [True, False])
async def test_cancel_or_wrong_state_preserves_credentials(tmp_path: Path, cancel: bool) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = tmp_path / "auth"
    path.write_text("original")
    services = Services(
        Settings(
            spotify_client_id="client", spotify_redirect_uri=f"http://127.0.0.1:{port}/callback"
        ),
        tmp_path / "db",
        path,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        headers = {"X-TuneTrail-Token": token}
        assert (
            await client.post(
                "/api/spotify/connect", json={"replace_existing": True}, headers=headers
            )
        ).status_code == 200
        if cancel:
            await client.post("/api/spotify/connect/cancel", json={}, headers=headers)
        else:
            async with httpx.AsyncClient() as browser:
                await browser.get(
                    f"http://127.0.0.1:{port}/callback", params={"code": "test", "state": "wrong"}
                )
            assert services.connect_task is not None
            await asyncio.wait_for(services.connect_task, 2)
        assert services.connect_status == ("cancelled" if cancel else "failed")
        assert path.read_text() == "original"
    await services.http.aclose()
