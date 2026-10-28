"""Local origin and mutation protection without network access."""

from pathlib import Path

import httpx
import pytest

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
