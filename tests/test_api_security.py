"""Local origin and mutation protection without network access."""

import sqlite3
from pathlib import Path

import httpx
import pytest

from tests.test_api_flow import Parser
from tunetrail.api.app import Services, create_app
from tunetrail.config import Settings
from tunetrail.persistence.sqlite_store import IncompatibleProfileError


async def test_host_origin_and_mutation_guards(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    app = create_app(services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766"
    ) as client:
        session = await client.get("/api/session")
        assert session.status_code == 200
        token = session.json()["token"]
        assert (
            await client.get("/api/session", headers={"host": "evil.test:8766"})
        ).status_code == 403
        assert (
            await client.get("/api/session", headers={"origin": "http://evil.test"})
        ).status_code == 403
        assert (await client.post("/api/chat", json={"message": "hi"})).status_code == 403
        headers = {"x-tunetrail-token": token, "origin": "http://evil.test"}
        assert (
            await client.post("/api/chat", headers=headers, json={"message": "hi"})
        ).status_code == 403
        headers = {"x-tunetrail-token": token, "sec-fetch-site": "cross-site"}
        assert (
            await client.post("/api/chat", headers=headers, json={"message": "hi"})
        ).status_code == 403
        assert (
            await client.post("/api/chat", headers={"x-tunetrail-token": token}, content="hi")
        ).status_code == 415
        assert (
            await client.post(
                "/api/chat", headers={"x-tunetrail-token": token}, json={"message": "x" * 70000}
            )
        ).status_code == 413
        headers = {"x-tunetrail-token": token}
        assert (
            await client.post(
                "/api/discovery",
                headers=headers,
                json={
                    "seeds": [{"kind": "recording", "musicbrainz_id": "spotify:track:abc"}],
                    "independently_obtained": True,
                },
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/discovery",
                headers=headers,
                json={
                    "seeds": [
                        {
                            "kind": "recording",
                            "musicbrainz_id": "00000000-0000-0000-0000-000000000001",
                            "title": "Spotify metadata",
                        }
                    ],
                    "independently_obtained": True,
                },
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/preview",
                headers=headers,
                json={
                    "discovery_id": "missing",
                    "intent": {},
                    "candidates": [{"title": "Spotify metadata"}],
                },
            )
        ).status_code == 422
    await services.http.aclose()


def test_incompatible_database_is_not_replaced(tmp_path: Path) -> None:
    database = tmp_path / "db"
    original = b"existing owner data"
    database.write_bytes(original)
    with pytest.raises(IncompatibleProfileError):
        Services(Settings(), database, tmp_path / "auth")
    assert database.read_bytes() == original


async def test_frontend_assets_share_origin_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tunetrail.api.app as api_app

    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>TuneTrail</h1>")
    (dist / "assets" / "app.js").write_text("console.log('local')")
    monkeypatch.setattr(api_app, "__file__", str(tmp_path / "src" / "tunetrail" / "api" / "app.py"))
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/assets/app.js")).status_code == 200
        assert (await client.get("/", headers={"origin": "http://evil.test"})).status_code == 403
        assert (
            await client.get("/assets/app.js", headers={"host": "evil.test:8766"})
        ).status_code == 403
    await services.http.aclose()


async def test_raw_chat_is_not_stored_in_profile_database(tmp_path: Path) -> None:
    database = tmp_path / "db"
    services = Services(Settings(), database, tmp_path / "auth")
    services.parser = Parser()
    secret_chat = "private raw message sentinel not a preference"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        headers = {"x-tunetrail-token": (await client.get("/api/session")).json()["token"]}
        assert (
            await client.post("/api/chat", headers=headers, json={"message": secret_chat})
        ).status_code == 200
        assert (await client.get("/api/preferences")).json()[0]["name"] == "Artist"
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())
    assert secret_chat not in dump
    await services.http.aclose()
