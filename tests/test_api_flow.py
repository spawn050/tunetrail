"""Mocked independent discovery through explicit playback approval."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import respx

from tests.test_spotify_models import track_payload
from tunetrail.api.app import Services, create_app
from tunetrail.auth.models import OAuthToken
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.domain.models import ChatInterpretation, ChatPreference, PreferenceSubject
from tunetrail.musicbrainz.models import Artist, Recording
from tunetrail.recommendation.independent_discovery import IndependentDiscovery


class Source:
    async def recording(self, recording_id: UUID) -> Recording:
        return Recording(
            id=str(recording_id),
            title="Track",
            duration_ms=180000,
            artists=[Artist(id=str(UUID(int=2)), name="Artist")],
        )

    async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
        return []


class Parser:
    async def parse(self, request: str) -> ChatInterpretation:
        return ChatInterpretation(
            preferences=[
                ChatPreference(subject=PreferenceSubject.ARTIST, name="Artist", strength=1)
            ]
        )


@respx.mock
async def test_catalog_search_is_read_only_and_limited_to_artist_and_track(tmp_path: Path) -> None:
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
    route = respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "artists": {"items": [{"id": "abc", "uri": "spotify:artist:abc", "name": "Artist"}]}
            },
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        response = await client.get("/api/catalog-search", params={"q": "Artist", "kind": "artist"})
        assert response.status_code == 200
        assert response.json()[0]["title"] == "Artist"
        assert route.called
        assert (
            await client.get("/api/catalog-search", params={"q": "x", "kind": "playlist"})
        ).status_code == 400
    await services.http.aclose()


@respx.mock
async def test_chat_discovery_preview_and_approved_playback(tmp_path: Path) -> None:
    settings = Settings(spotify_api_url="https://api.example/v1")
    services = Services(settings, tmp_path / "db", tmp_path / "auth")
    services.discovery = IndependentDiscovery(Source())
    services.parser = Parser()
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="secret-access",
            refresh_token="secret-refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    track = {**track_payload(), "is_playable": True}
    search = respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 1}})
    )
    play = respx.put("https://api.example/v1/me/player/play").mock(return_value=httpx.Response(204))
    respx.get("https://api.example/v1/me/player").mock(return_value=httpx.Response(204))
    app = create_app(services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        headers = {"x-tunetrail-token": token}
        response = await client.post(
            "/api/chat", headers=headers, json={"message": "I like Artist"}
        )
        assert response.status_code == 200
        assert (await client.get("/api/preferences")).json()[0]["strength"] == 1
        data = {
            "seeds": [{"kind": "recording", "musicbrainz_id": str(UUID(int=1))}],
            "limit": 1,
            "independently_obtained": True,
        }
        response = await client.post("/api/discovery", headers=headers, json=data)
        assert response.status_code == 200
        discovery = response.json()["discovery_id"]
        response = await client.post(
            "/api/preview",
            headers=headers,
            json={"discovery_id": discovery, "intent": {"queue_size": 1}, "device_id": "device"},
        )
        assert response.status_code == 200
        preview = response.json()
        assert preview["matches"][0]["ranked"]["score"] == 1
        assert search.called and not play.called
        assert "secret-access" not in response.text and "secret-refresh" not in response.text
        proposal = preview["proposal_id"]
        response = await client.post(
            "/api/approval", headers=headers, json={"proposal_id": proposal}
        )
        assert response.status_code == 422 and not play.called
        response = await client.post(
            "/api/approval",
            headers=headers,
            json={"proposal_id": proposal, "explicitly_approve": 1},
        )
        assert response.status_code == 422 and not play.called
        response = await client.post(
            "/api/approval",
            headers=headers,
            json={"proposal_id": proposal, "explicitly_approve": True, "device_id": "different"},
        )
        assert response.status_code == 422 and not play.called
        response = await client.post(
            "/api/approval",
            headers=headers,
            json={"proposal_id": proposal, "explicitly_approve": True},
        )
        assert response.status_code == 200 and play.call_count == 1
        assert (
            await client.post(
                "/api/approval",
                headers=headers,
                json={"proposal_id": proposal, "explicitly_approve": True},
            )
        ).status_code == 404
        assert play.call_count == 1
    await services.http.aclose()


@respx.mock
async def test_failed_player_request_cannot_reuse_approval(tmp_path: Path) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    services.discovery = IndependentDiscovery(Source())
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    track = {**track_payload(), "is_playable": True}
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={"tracks": {"items": [track], "total": 1}},
        )
    )
    play = respx.put("https://api.example/v1/me/player/play").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        headers = {"x-tunetrail-token": (await client.get("/api/session")).json()["token"]}
        discovery = await client.post(
            "/api/discovery",
            headers=headers,
            json={
                "seeds": [{"kind": "recording", "musicbrainz_id": str(UUID(int=1))}],
                "limit": 1,
                "independently_obtained": True,
            },
        )
        preview = await client.post(
            "/api/preview",
            headers=headers,
            json={
                "discovery_id": discovery.json()["discovery_id"],
                "intent": {"queue_size": 1},
            },
        )
        approval = {"proposal_id": preview.json()["proposal_id"], "explicitly_approve": True}
        assert (
            await client.post("/api/approval", headers=headers, json=approval)
        ).status_code == 502
        assert (
            await client.post("/api/approval", headers=headers, json=approval)
        ).status_code == 404
        assert play.call_count == 1
    await services.http.aclose()


@respx.mock
async def test_cancel_expiry_source_failure_and_profile_controls(tmp_path: Path) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    services.discovery = IndependentDiscovery(Source())
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    track = {**track_payload(), "is_playable": True}
    search = respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 1}})
    )
    app = create_app(services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766"
    ) as client:
        headers = {"x-tunetrail-token": (await client.get("/api/session")).json()["token"]}
        preference = {"subject": "artist", "name": "Artist", "strength": -1}
        assert (
            await client.post("/api/preferences", headers=headers, json=preference)
        ).status_code == 200
        assert (
            await client.post(
                "/api/feedback",
                headers=headers,
                json={"subject": "artist", "name": "Artist", "response": "skip"},
            )
        ).status_code == 200
        assert (await client.get("/api/preferences")).json()[0]["strength"] == -1
        assert (
            await client.post(
                "/api/preferences/reset", headers=headers, json={"confirm_reset": False}
            )
        ).status_code == 422
        discovery_body = {
            "seeds": [{"kind": "recording", "musicbrainz_id": str(UUID(int=1))}],
            "limit": 1,
        }
        assert (
            await client.post("/api/discovery", headers=headers, json=discovery_body)
        ).status_code == 422
        discovery_body["independently_obtained"] = True
        discovered = await client.post("/api/discovery", headers=headers, json=discovery_body)
        discovery = discovered.json()["discovery_id"]
        preview_body = {"discovery_id": discovery, "intent": {"queue_size": 1}}
        preview = (await client.post("/api/preview", headers=headers, json=preview_body)).json()
        proposal = preview["proposal_id"]
        unsupported = await client.post(
            "/api/preview",
            headers=headers,
            json={"discovery_id": discovery, "intent": {"year_start": 2000}},
        )
        assert unsupported.json()["proposal_id"] is None
        assert (
            await client.post("/api/cancel", headers=headers, json={"proposal_id": proposal})
        ).status_code == 200
        assert (
            await client.post(
                "/api/approval",
                headers=headers,
                json={"proposal_id": proposal, "explicitly_approve": True},
            )
        ).status_code == 404
        search.mock(return_value=httpx.Response(503))
        preview = (await client.post("/api/preview", headers=headers, json=preview_body)).json()
        assert preview["proposal_id"] is None and preview["issues"]
        services.discoveries[discovery] = (0, services.discoveries[discovery][1])
        assert (
            await client.post("/api/preview", headers=headers, json=preview_body)
        ).status_code == 404
        assert (
            await client.post(
                "/api/preferences/reset", headers=headers, json={"confirm_reset": True}
            )
        ).status_code == 200
        assert (await client.get("/api/preferences")).json() == []
    await services.http.aclose()


@respx.mock
async def test_feedback_to_queue_preview_cancel_and_single_approval(tmp_path: Path) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    services.discovery = IndependentDiscovery(Source())
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(
            200, json={"tracks": {"items": [{**track_payload(), "is_playable": True}], "total": 1}}
        )
    )
    queue = respx.post("https://api.example/v1/me/player/queue").mock(
        return_value=httpx.Response(204)
    )
    respx.get("https://api.example/v1/me/player").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        headers = {"x-tunetrail-token": (await client.get("/api/session")).json()["token"]}
        discovery = await client.post(
            "/api/discovery",
            headers=headers,
            json={
                "seeds": [{"kind": "recording", "musicbrainz_id": str(UUID(int=1))}],
                "limit": 1,
                "independently_obtained": True,
            },
        )
        assert discovery.status_code == 200
        feedback = await client.post(
            "/api/feedback",
            headers=headers,
            json={
                "subject": "recording",
                "name": "Track",
                "artist": "Artist",
                "musicbrainz_id": str(UUID(int=1)),
                "response": "like",
                "source": "survey",
            },
        )
        assert feedback.status_code == 200
        body = {
            "discovery_id": discovery.json()["discovery_id"],
            "intent": {"queue_size": 1, "action": "append_queue"},
        }
        first = (await client.post("/api/preview", headers=headers, json=body)).json()
        assert first["matches"][0]["ranked"]["score"] == 1
        assert first["proposal_id"] and not queue.called
        assert (
            await client.post(
                "/api/cancel", headers=headers, json={"proposal_id": first["proposal_id"]}
            )
        ).status_code == 200
        assert (
            await client.post(
                "/api/approval",
                headers=headers,
                json={"proposal_id": first["proposal_id"], "explicitly_approve": True},
            )
        ).status_code == 404
        assert not queue.called
        second = (await client.post("/api/preview", headers=headers, json=body)).json()
        approval = {"proposal_id": second["proposal_id"], "explicitly_approve": True}
        assert (
            await client.post("/api/approval", headers=headers, json=approval)
        ).status_code == 200
        assert (
            await client.post("/api/approval", headers=headers, json=approval)
        ).status_code == 404
        assert queue.call_count == 1
    await services.http.aclose()
