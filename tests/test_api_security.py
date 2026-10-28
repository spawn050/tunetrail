"""Local origin and mutation protection without network access."""

from pathlib import Path

import httpx

from tunetrail.api.app import Services, create_app
from tunetrail.config import Settings


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
    await services.http.aclose()
