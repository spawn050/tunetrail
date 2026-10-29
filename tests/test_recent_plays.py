"""Offline API tests for the bounded recent-plays chat operation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from tunetrail.api.app import Services, create_app
from tunetrail.auth.models import OAuthToken
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.domain.models import ChatInterpretation, RecentPlaysQuery


class HistoryParser:
    def __init__(self, limit: int = 1) -> None:
        self.requests: list[str] = []
        self.limit = limit

    async def parse(self, request: str) -> ChatInterpretation:
        self.requests.append(request)
        return ChatInterpretation(
            recent_plays=RecentPlaysQuery(period="last_7_days", limit=self.limit)
        )


@respx.mock
async def test_recent_plays_stay_local_and_are_bounded(tmp_path: Path) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    parser = HistoryParser()
    services.parser = parser
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="secret",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    def item(days: int, title: str) -> dict[str, object]:
        return {
            "played_at": (datetime.now(UTC) - timedelta(days=days)).isoformat(),
            "track": {"name": title, "artists": [{"name": "Private artist"}], "id": "private-id"},
        }

    route = respx.get("https://api.example/v1/me/player/recently-played").mock(
        return_value=httpx.Response(
            200, json={"items": [item(1, "Private song"), item(2, "Other"), item(8, "Old")]}
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        response = await client.post(
            "/api/chat",
            headers={"x-tunetrail-token": token},
            json={"message": "What did I listen to last week?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["recent_plays"]) == 1
        assert data["recent_plays"][0]["title"] == "Private song"
        assert set(data["recent_plays"][0]) == {"title", "artists", "played_at"}
        assert "may not cover" in data["notice"]
        assert parser.requests == ["What did I listen to last week?"]
        assert route.call_count == 1
        assert len(respx.calls) == 1
    await services.http.aclose()


@respx.mock
async def test_recent_plays_filters_old_results_and_reports_provider_failure(
    tmp_path: Path,
) -> None:
    services = Services(
        Settings(spotify_api_url="https://api.example/v1"), tmp_path / "db", tmp_path / "auth"
    )
    services.parser = HistoryParser(limit=50)
    await FileCredentialStore(tmp_path / "auth").save_token(
        OAuthToken(
            access_token="secret",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    track = {"name": "Song", "artists": [{"name": "Artist"}]}
    route = respx.get("https://api.example/v1/me/player/recently-played").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "track": track,
                        "played_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                    },
                    {
                        "track": track,
                        "played_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
                    },
                ]
            },
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        headers = {"x-tunetrail-token": token}
        response = await client.post("/api/chat", headers=headers, json={"message": "last week"})
        assert len(response.json()["recent_plays"]) == 1
        route.mock(return_value=httpx.Response(403))
        response = await client.post("/api/chat", headers=headers, json={"message": "last week"})
        assert response.status_code == 502
        assert "scopes" in response.json()["detail"]
    await services.http.aclose()


def test_recent_query_rejects_unsafe_or_mixed_requests() -> None:
    with pytest.raises(ValidationError):
        RecentPlaysQuery.model_validate({"period": "last_7_days", "limit": 51})
    with pytest.raises(ValidationError):
        RecentPlaysQuery.model_validate({"period": "last_7_days", "sql": "DELETE"})
    with pytest.raises(ValidationError):
        ChatInterpretation.model_validate(
            {"recent_plays": {"period": "recent"}, "clarification": "Maybe?"}
        )
