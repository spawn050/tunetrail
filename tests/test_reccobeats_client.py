"""Offline ReccoBeats integration, provenance and outage checks."""

import asyncio
from typing import cast
from uuid import UUID

import httpx
import pytest

from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.musicbrainz.client import MusicBrainzClient, RateLimiter
from tunetrail.musicbrainz.models import Artist, Recording
from tunetrail.reccobeats.client import ReccoBeatsEnricher
from tunetrail.recommendation.independent_discovery import (
    IndependentCandidate,
    IndependentDiscovery,
    IndependentSeed,
    SeedKind,
)

MBID = UUID("11111111-1111-4111-8111-111111111111")
RBID = UUID("22222222-2222-4222-8222-222222222222")
ARTIST_ID = UUID("33333333-3333-4333-8333-333333333333")


def candidate(title: str = "A Song", duration: int | None = 180000) -> IndependentCandidate:
    return IndependentCandidate(MBID, title, ("An Artist",), duration)


def page() -> dict[str, object]:
    return {
        "content": [
            {
                "id": str(RBID),
                "trackTitle": "A Song",
                "durationMs": 180000,
                "artists": [{"name": "An Artist", "href": "spotify:artist:SECRET"}],
                "href": "spotify:track:SECRET",
                "isrc": "SECRETISRC",
            }
        ],
        "totalElements": 1,
        "totalPages": 1,
        "page": 0,
    }


async def test_enrichment_sends_only_independent_fields_and_caches() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=page())
        return httpx.Response(
            200,
            json={
                "id": str(RBID),
                "energy": 0.7,
                "tempo": 120.0,
                "key": -1,
                "danceability": None,
                "mode": 1,
                "loudness": -7.5,
                "href": "spotify:track:SECRET",
                "isrc": "SECRETISRC",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = ReccoBeatsEnricher(http)
        result = await service.enrich(candidate())
        cached = await service.enrich(candidate())
    assert result == cached and result.candidate == candidate()
    assert result.features is not None and result.features.source == "reccobeats"
    assert dict(result.features.values) == {
        "energy": 0.7,
        "tempo": 120.0,
        "mode": 1,
        "loudness": -7.5,
    }
    assert len(calls) == 2
    assert calls[0].url.path == "/v1/track/search"
    assert dict(calls[0].url.params) == {
        "searchText": "A Song",
        "artist": "An Artist",
        "page": "0",
        "size": "10",
    }
    assert calls[1].url.path == f"/v1/track/{RBID}/audio-features"
    assert all("SECRET" not in str(request.url) for request in calls)


@pytest.mark.parametrize("mode", ["ambiguous", "missing", "invalid", "wrong_id", "error"])
async def test_ambiguous_missing_and_failed_features_are_unknown(mode: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if mode == "error":
            return httpx.Response(503)
        if request.url.path.endswith("/search"):
            result = page()
            if mode == "ambiguous":
                result["totalElements"] = 2
                result["totalPages"] = 2
            return httpx.Response(200, json=result)
        body: dict[str, object] = {"id": str(RBID)}
        if mode == "invalid":
            body.update({"energy": 2, "danceability": True, "key": -1})
        if mode == "wrong_id":
            body.update({"id": str(MBID), "energy": 0.5})
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ReccoBeatsEnricher(http).enrich(candidate())
    assert result.candidate == candidate() and result.features is None
    assert len(calls) == (1 if mode in ("ambiguous", "error") else 2)


async def test_rate_limit_blocks_followup_and_does_not_cache_failure() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = ReccoBeatsEnricher(http)
        assert (await service.enrich(candidate())).status == "source unavailable"
        assert (await service.enrich(candidate())).status == "source unavailable"
    assert len(calls) == 1


async def test_timeout_returns_original_candidate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ReccoBeatsEnricher(http, timeout_seconds=0.001).enrich(candidate())
    assert result.candidate == candidate() and result.features is None


async def test_rejects_spotify_inputs_and_short_or_missing_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected outbound request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = ReccoBeatsEnricher(http)
        spotify = Candidate(
            source=CandidateSource.SPOTIFY, source_id="x", title="Song", artists=["Artist"]
        )
        with pytest.raises(ValueError):
            await service.enrich(cast(IndependentCandidate, spotify))
        with pytest.raises(ValueError):
            await service.enrich(candidate("spotify:track:secret"))
        with pytest.raises(ValueError):
            await service.enrich(
                IndependentCandidate(MBID, "A Song", ("An Artist",), 180000, "spotify")
            )
        assert (await service.enrich(candidate("Hi"))).features is None
        assert (await service.enrich(candidate(duration=None))).features is None


async def test_cache_is_bounded_and_does_not_store_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr("tunetrail.reccobeats.client.time.monotonic", lambda: clock[0])
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        clock[0] += 1
        return httpx.Response(
            200,
            json=page()
            if request.url.path.endswith("/search")
            else {"id": str(RBID), "energy": 0.5},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = ReccoBeatsEnricher(http, cache_size=1, cache_ttl_seconds=5)
        await service.enrich(candidate())
        await service.enrich(IndependentCandidate(UUID(int=4), "A Song", ("An Artist",), 180000))
        await service.enrich(candidate())
        assert len(calls) == 6
        clock[0] += 6
        await service.enrich(candidate())
        assert len(calls) == 8


async def test_discovery_composes_optional_enrichment() -> None:
    class Source:
        async def recording(self, recording_id: UUID) -> Recording:
            return Recording(
                id=str(MBID),
                title="A Song",
                duration_ms=180000,
                artists=[Artist(id=str(ARTIST_ID), name="An Artist")],
            )

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            return []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=page()
            if request.url.path.endswith("/search")
            else {"id": str(RBID), "energy": 0.5},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await IndependentDiscovery(Source()).discover(
            [IndependentSeed(SeedKind.RECORDING, MBID)], limit=1, enricher=ReccoBeatsEnricher(http)
        )
    assert result.enrichment_status == "requested"
    assert len(result.enriched) == 1 and result.enriched[0].features is not None


async def test_failed_search_is_not_cached_and_redirect_is_not_followed() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(302, headers={"Location": "https://example.com/collect"})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=page())
        return httpx.Response(200, json={"id": str(RBID), "energy": 0.5})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        service = ReccoBeatsEnricher(http)
        assert (await service.enrich(candidate())).features is None
        assert (await service.enrich(candidate())).features is not None
    assert len(calls) == 3
    assert all(request.url.host == "api.reccobeats.com" for request in calls)


async def test_rate_limit_on_feature_request_blocks_next_search() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=page())
        return httpx.Response(429, headers={"Retry-After": "120"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = ReccoBeatsEnricher(http)
        assert (await service.enrich(candidate())).features is None
        assert (await service.enrich(candidate())).status == "source unavailable"
    assert len(calls) == 2


async def test_shared_http_client_does_not_leak_credentials_or_parameters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=("user", "password"),
        cookies={"token": "secret"},
        headers={"Authorization": "Bearer secret", "X-Secret": "secret"},
        params={"token": "secret"},
    ) as http:
        await ReccoBeatsEnricher(http).enrich(candidate())
    assert len(requests) == 1
    assert "secret" not in str(requests[0].url)
    assert "authorization" not in requests[0].headers
    assert "cookie" not in requests[0].headers
    assert "x-secret" not in requests[0].headers


async def test_end_to_end_requests_exclude_provider_spotify_metadata() -> None:
    calls: list[httpx.Request] = []

    class ImmediateLimiter:
        async def get(
            self, client: httpx.AsyncClient, url: str, params: dict[str, str]
        ) -> httpx.Response:
            return await client.get(url, params=params)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "musicbrainz.org":
            return httpx.Response(
                200,
                json={
                    "id": str(MBID),
                    "title": "A Song",
                    "length": 180000,
                    "artist-credit": [{"artist": {"id": str(ARTIST_ID), "name": "An Artist"}}],
                    "relations": [{"url": {"resource": "spotify:track:SECRET"}}],
                },
            )
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=page())
        return httpx.Response(
            200, json={"id": str(RBID), "energy": 0.5, "href": "spotify:track:SECRET"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        musicbrainz = MusicBrainzClient(http, limiter=cast(RateLimiter, ImmediateLimiter()))
        result = await IndependentDiscovery(musicbrainz).discover(
            [IndependentSeed(SeedKind.RECORDING, MBID)], limit=1, enricher=ReccoBeatsEnricher(http)
        )
    assert result.enriched[0].features is not None
    assert [request.url.host for request in calls] == [
        "musicbrainz.org",
        "api.reccobeats.com",
        "api.reccobeats.com",
    ]
    assert all("SECRET" not in str(request.url) for request in calls)
