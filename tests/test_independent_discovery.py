"""Offline independent discovery and outbound boundary regression tests."""

import asyncio
from typing import cast
from uuid import UUID

import httpx
import pytest

from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.musicbrainz.client import MusicBrainzClient, MusicBrainzError, RateLimiter
from tunetrail.musicbrainz.models import Artist, Recording
from tunetrail.recommendation.independent_discovery import (
    IndependentDiscovery,
    IndependentSeed,
    SeedKind,
)

ARTIST = UUID("11111111-1111-4111-8111-111111111111")
SONG = UUID("22222222-2222-4222-8222-222222222222")


class ImmediateLimiter:
    async def get(
        self, client: httpx.AsyncClient, url: str, params: dict[str, str]
    ) -> httpx.Response:
        return await client.get(url, params=params)


async def test_browse_sends_only_independent_id_and_returns_bounded_metadata() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "recording-count": 100,
                "recording-offset": 0,
                "recordings": [
                    {
                        "id": str(SONG),
                        "title": "Song",
                        "length": 180000,
                        "artist-credit": [{"artist": {"id": str(ARTIST), "name": "Artist"}}],
                        "isrcs": ["USABC1234567"],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MusicBrainzClient(http, limiter=cast(RateLimiter, ImmediateLimiter()))
        result = await IndependentDiscovery(client).discover(
            [IndependentSeed(SeedKind.ARTIST, ARTIST)], limit=2
        )
    assert len(result.candidates) == 1 and result.needs_more_seeds
    assert result.candidates[0].recording_id == SONG
    assert result.candidates[0].source == "musicbrainz"
    assert "unavailable" in result.enrichment_status
    assert len(calls) == 1
    assert calls[0].url.path == "/ws/2/recording"
    assert dict(calls[0].url.params) == {
        "artist": str(ARTIST),
        "limit": "2",
        "inc": "artist-credits",
        "fmt": "json",
    }


async def test_recording_lookup_and_bad_identity() -> None:
    class Source:
        async def recording(self, recording_id: UUID) -> Recording:
            return Recording(id=str(SONG), title="Song", artists=[Artist(id=str(ARTIST), name="A")])

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            return [await self.recording(SONG)]

    service = IndependentDiscovery(Source())
    assert (
        len(
            (
                await service.discover([IndependentSeed(SeedKind.RECORDING, SONG)], limit=1)
            ).candidates
        )
        == 1
    )
    assert not (await service.discover([IndependentSeed(SeedKind.ARTIST, SONG)])).candidates
    assert not (await service.discover([IndependentSeed(SeedKind.RECORDING, ARTIST)])).candidates


async def test_source_failure_timeout_and_no_seeds() -> None:
    class Source:
        async def recording(self, recording_id: UUID) -> Recording:
            if recording_id == SONG:
                raise MusicBrainzError("unavailable")
            await asyncio.sleep(1)
            raise AssertionError("timeout expected")

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            return []

    service = IndependentDiscovery(Source(), timeout_seconds=0.001)
    result = await service.discover(
        [IndependentSeed(SeedKind.RECORDING, SONG), IndependentSeed(SeedKind.RECORDING, ARTIST)]
    )
    assert result.source_failures == 2 and result.needs_more_seeds and not result.candidates
    assert (await service.discover([])).needs_more_seeds


async def test_rejects_spotify_candidates_before_any_request() -> None:
    class Source:
        async def recording(self, recording_id: UUID) -> Recording:
            raise AssertionError("unexpected request")

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            raise AssertionError("unexpected request")

    spotify = Candidate(
        source=CandidateSource.SPOTIFY,
        source_id="spotify:track:abc",
        title="Song",
        artists=["A"],
    )
    with pytest.raises(ValueError, match="independently supplied"):
        await IndependentDiscovery(Source()).discover(cast(list[IndependentSeed], [spotify]))
    with pytest.raises(ValueError):
        IndependentSeed(SeedKind.ARTIST, cast(UUID, "spotify:artist:abc"))


async def test_browse_rejects_malformed_page() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"recordings": []}))
    ) as http:
        client = MusicBrainzClient(http, limiter=cast(RateLimiter, ImmediateLimiter()))
        result = await IndependentDiscovery(client).discover(
            [IndependentSeed(SeedKind.ARTIST, ARTIST)]
        )
    assert result.source_failures == 1 and not result.candidates


async def test_seed_and_result_limits() -> None:
    class Source:
        def __init__(self) -> None:
            self.calls = 0

        async def recording(self, recording_id: UUID) -> Recording:
            self.calls += 1
            return Recording(id=str(SONG), title="Song", artists=[Artist(id=str(ARTIST), name="A")])

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            raise AssertionError("unexpected request")

    source = Source()
    service = IndependentDiscovery(source)
    seed = IndependentSeed(SeedKind.RECORDING, SONG)
    result = await service.discover([seed, seed], limit=2)
    assert source.calls == 1 and len(result.candidates) == 1
    with pytest.raises(ValueError):
        await service.discover([seed] * 6)
    with pytest.raises(ValueError):
        await service.discover([seed], limit=21)


async def test_partial_failure_preserves_results_and_deduplicates() -> None:
    class Source:
        async def recording(self, recording_id: UUID) -> Recording:
            raise MusicBrainzError("unavailable")

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            item = Recording(id=str(SONG), title="Song", artists=[Artist(id=str(ARTIST), name="A")])
            return [item, item]

    result = await IndependentDiscovery(Source()).discover(
        [IndependentSeed(SeedKind.ARTIST, ARTIST), IndependentSeed(SeedKind.RECORDING, ARTIST)],
        limit=3,
    )
    assert len(result.candidates) == 1 and result.source_failures == 1 and result.needs_more_seeds


async def test_recording_request_contains_only_musicbrainz_id() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": str(SONG),
                "title": "Song",
                "artist-credit": [{"artist": {"id": str(ARTIST), "name": "A"}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MusicBrainzClient(http, limiter=cast(RateLimiter, ImmediateLimiter()))
        result = await IndependentDiscovery(client).discover(
            [IndependentSeed(SeedKind.RECORDING, SONG)], limit=1
        )
    assert len(result.candidates) == 1 and not result.needs_more_seeds
    assert calls[0].url.path == f"/ws/2/recording/{SONG}"
    assert dict(calls[0].url.params) == {"inc": "artist-credits", "fmt": "json"}
