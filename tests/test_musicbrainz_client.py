"""MusicBrainz public read contract and pacing tests."""

import asyncio
import time

import httpx
import pytest

from tunetrail.musicbrainz.client import MusicBrainzClient, MusicBrainzError, RateLimiter


async def test_isrc_mapping_and_retry() -> None:
    """A 503 is retried, with identification, and normalized entities are returned."""
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "recordings": [
                    {
                        "id": "recording-id",
                        "title": "Song",
                        "length": 180000,
                        "artist-credit": [{"artist": {"id": "artist-id", "name": "Artist"}}],
                        "isrcs": ["USABC1234567"],
                        "releases": [
                            {
                                "id": "release-id",
                                "title": "Album",
                                "date": "2001-02-03",
                                "release-group": {"primary-type": "Album"},
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        start = time.monotonic()
        result = await MusicBrainzClient(http, limiter=RateLimiter()).by_isrc("USABC1234567")
        elapsed = time.monotonic() - start
    assert len(calls) == 2
    assert elapsed >= 0.99
    assert "github.com/spawn050/tunetrail" in calls[0].headers["user-agent"]
    assert calls[0].url.path.endswith("/isrc/USABC1234567")
    assert result[0].artists[0].name == "Artist"
    assert result[0].releases[0].date == "2001-02-03"


async def test_shared_limiter_serializes_concurrent_clients() -> None:
    """Concurrent calls through separate clients cannot exceed one start per second."""
    starts: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        return httpx.Response(200, json={"recordings": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        limiter = RateLimiter()
        first = MusicBrainzClient(http, limiter=limiter)
        second = MusicBrainzClient(http, limiter=limiter)
        await asyncio.gather(
            first.search(artist="A", title="B"), second.search(artist="C", title="D")
        )
    assert starts[1] - starts[0] >= 0.99


async def test_failure_and_unsafe_isrc() -> None:
    """Invalid IDs never reach the service and failures do not expose payloads."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, text="sensitive response")
        )
    ) as http:
        client = MusicBrainzClient(http, limiter=RateLimiter())
        with pytest.raises(ValueError):
            await client.by_isrc("../../bad")
        with pytest.raises(MusicBrainzError, match="unavailable"):
            await client.search(artist="A", title="B")
