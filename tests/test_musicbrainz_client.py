"""MusicBrainz public read contract and pacing tests."""

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

from tunetrail.musicbrainz.client import MusicBrainzClient, MusicBrainzError, RateLimiter


async def test_isrc_mapping_and_retry(tmp_path: Path) -> None:
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
                        "release-count": 1,
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
        result = await MusicBrainzClient(http, limiter=RateLimiter(tmp_path / "lock")).by_isrc(
            "USABC1234567"
        )
        elapsed = time.monotonic() - start
    assert len(calls) == 2
    assert elapsed >= 0.99
    assert "github.com/spawn050/tunetrail" in calls[0].headers["user-agent"]
    assert calls[0].url.path.endswith("/isrc/USABC1234567")
    assert result[0].artists[0].name == "Artist"
    assert result[0].releases[0].date == "2001-02-03"
    assert result[0].releases_complete


async def test_missing_release_count_is_not_assumed_complete(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"recordings": [{"id": "one", "title": "Song", "releases": []}]}
            )
        )
    ) as http:
        result = await MusicBrainzClient(http, limiter=RateLimiter(tmp_path / "lock")).search(
            artist="Artist", title="Song"
        )
    assert not result[0].releases_complete


async def test_shared_limiter_serializes_concurrent_clients(tmp_path: Path) -> None:
    """Concurrent calls through separate clients cannot exceed one start per second."""
    starts: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        return httpx.Response(200, json={"recordings": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = MusicBrainzClient(http, limiter=RateLimiter(tmp_path / "lock"))
        second = MusicBrainzClient(http, limiter=RateLimiter(tmp_path / "lock"))
        await asyncio.gather(
            first.search(artist="A", title="B"), second.search(artist="C", title="D")
        )
    assert starts[1] - starts[0] >= 0.99


async def test_rate_limit_is_shared_across_processes(tmp_path: Path) -> None:
    """Independent processes sharing the lock cannot start requests within a second."""
    script = """
import asyncio, sys, time
from pathlib import Path
import httpx
from tunetrail.musicbrainz.client import RateLimiter
async def main():
    async def handle(request):
        print(time.monotonic(), flush=True)
        return httpx.Response(200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await RateLimiter(Path(sys.argv[1])).get(client, 'https://example.org', {})
asyncio.run(main())
"""
    lock = tmp_path / "rate.lock"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, str(lock), stdout=asyncio.subprocess.PIPE
    )
    assert process.stdout is not None
    start = float(await process.stdout.readline())
    assert await process.wait() == 0
    times: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        times.append(time.monotonic())
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await RateLimiter(lock).get(http, "https://example.org", {})
    assert times[0] - start >= 0.99


async def test_failure_and_unsafe_isrc(tmp_path: Path) -> None:
    """Invalid IDs never reach the service and failures do not expose payloads."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, text="sensitive response")
        )
    ) as http:
        client = MusicBrainzClient(http, limiter=RateLimiter(tmp_path / "lock"))
        with pytest.raises(ValueError):
            await client.by_isrc("../../bad")
        with pytest.raises(MusicBrainzError, match="unavailable"):
            await client.search(artist="A", title="B")


async def test_missing_isrc_is_empty(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as http:
        assert (
            await MusicBrainzClient(http, limiter=RateLimiter(tmp_path / "lock")).by_isrc(
                "USABC1234567"
            )
            == []
        )
