"""Read-only recommendation chat using independent, mocked MusicBrainz data."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest

from tunetrail.api.app import Services, create_app
from tunetrail.config import Settings
from tunetrail.domain.models import ExplicitPreference, PreferenceSource, PreferenceSubject
from tunetrail.musicbrainz.client import (
    ArtistMatch,
    MusicBrainzClient,
    MusicBrainzError,
    RateLimiter,
)
from tunetrail.recommendation.independent_discovery import DiscoveryResult, IndependentCandidate

ARTIST_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_ID = UUID("22222222-2222-4222-8222-222222222222")


@pytest.mark.asyncio
async def test_recommendations_clarify_select_and_never_mutate(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    services.musicbrainz.search_artists_independent = AsyncMock(
        return_value=[
            ArtistMatch(ARTIST_ID, "Example", "singer"),
            ArtistMatch(OTHER_ID, "Example", "band"),
        ]
    )
    services.discovery.discover = AsyncMock(
        return_value=DiscoveryResult(
            (IndependentCandidate(OTHER_ID, "Song", ("Example",), None),),
            0,
            True,
        )
    )
    services.spotify.start_playback = AsyncMock()
    services.spotify.add_to_queue = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]

        async def chat(message: str, state: str | None = None) -> dict[str, object]:
            response = await client.post(
                "/api/chat",
                headers={"x-tunetrail-token": token},
                json={"message": message, **({"recommendation_state": state} if state else {})},
            )
            assert response.status_code == 200
            return response.json()  # type: ignore[no-any-return]

        initial = await chat("recommend me some songs that i would like")
        assert "artist:" in str(initial["recommendation_message"])
        first = str(initial["recommendation_state"])
        invalid = await chat("artist: https://open.spotify.com/artist/abc", first)
        services.musicbrainz.search_artists_independent.assert_not_called()
        result = await chat("artist: Example", str(invalid["recommendation_state"]))
        assert "singer" in str(result["recommendation_message"])
        assert "band" in str(result["recommendation_message"])
        choice = str(result["recommendation_state"])
        bad = await chat("select artist 3", choice)
        assert "Choose a number" in str(bad["recommendation_message"])
        answer = await chat("select artist 2", str(bad["recommendation_state"]))
        assert "Song — Example" in str(answer["recommendation_message"])
        assert "not proven similar" in str(answer["recommendation_message"])
        assert "Nothing was played or queued" in str(answer["recommendation_message"])
        assert services.discovery.discover.call_args.args[0][0].musicbrainz_id == OTHER_ID
        assert "expired" in str((await chat("select artist 2", choice))["recommendation_message"])
        assert await services.profile.list_preferences() == []
        services.spotify.start_playback.assert_not_awaited()
        services.spotify.add_to_queue.assert_not_awaited()
    await services.http.aclose()


@pytest.mark.asyncio
async def test_independent_artist_search_is_bounded_and_escaped() -> None:
    limiter = RateLimiter()
    limiter.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "artists": [
                    {"id": str(ARTIST_ID), "name": "Example", "disambiguation": "singer"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = MusicBrainzClient(http, limiter=limiter)
        result = await client.search_artists_independent('Example "name"')
        assert result[0].id == ARTIST_ID
        params = limiter.get.call_args.args[2]
        assert params["query"] == 'artist:"Example \\"name\\""'
        assert params["limit"] == "5"
        with pytest.raises(ValueError):
            await client.search_artists_independent("spotify:artist:abc")
        assert limiter.get.await_count == 1


@pytest.mark.asyncio
async def test_saved_artist_and_provider_failure(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    await services.profile.set_preference(
        ExplicitPreference(
            subject=PreferenceSubject.ARTIST,
            name="Example",
            strength=1,
            source=PreferenceSource.DIRECT,
            updated_at=datetime.now(UTC),
        )
    )
    services.musicbrainz.search_artists_independent = AsyncMock(side_effect=MusicBrainzError())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        response = await client.post(
            "/api/chat",
            headers={"x-tunetrail-token": token},
            json={"message": "recommend me some songs"},
        )
        assert "unavailable" in response.json()["recommendation_message"]
        services.musicbrainz.search_artists_independent.assert_awaited_once_with("Example")
    await services.http.aclose()
