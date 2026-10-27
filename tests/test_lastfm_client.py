"""Mocked tests for the optional typed Last.fm public API client."""

import httpx
import pytest
import respx

from tunetrail.lastfm.client import LastfmClient
from tunetrail.lastfm.errors import LastfmProfileError, LastfmRateLimitError


def client(http_client: httpx.AsyncClient, sleeps: list[float]) -> LastfmClient:
    """Create a client with an observable retry sleep and isolated API URL."""

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    return LastfmClient(
        api_key="lastfm-key",
        username="listener",
        http_client=http_client,
        api_url="https://lastfm.example/2.0/",
        sleep=sleep,
    )


@respx.mock
async def test_client_maps_profile_similarity_and_tag_responses() -> None:
    """Public Last.fm endpoints become bounded typed signals with no raw payload retention."""
    route = respx.get("https://lastfm.example/2.0/").mock(
        side_effect=[
            httpx.Response(200, json={"topartists": {"artist": [{"name": "Artist"}]}}),
            httpx.Response(
                200,
                json={
                    "toptracks": {
                        "track": [
                            {
                                "name": "Track",
                                "artist": {"name": "Artist"},
                                "duration": "180000",
                                "url": "https://last.fm/track",
                            }
                        ]
                    }
                },
            ),
            httpx.Response(
                200,
                json={
                    "recenttracks": {"track": [{"name": "Recent", "artist": {"#text": "Artist"}}]}
                },
            ),
            httpx.Response(
                200,
                json={
                    "similartracks": {
                        "track": [{"name": "Similar", "artist": {"name": "Other"}, "match": "0.75"}]
                    }
                },
            ),
            httpx.Response(200, json={"similarartists": {"artist": [{"name": "Other"}]}}),
            httpx.Response(200, json={"toptags": {"tag": [{"name": "chill"}]}}),
        ]
    )
    async with httpx.AsyncClient() as http_client:
        lastfm = client(http_client, [])
        artists = await lastfm.user_top_artists()
        top_tracks = await lastfm.user_top_tracks()
        recent = await lastfm.user_recent_tracks()
        similar = await lastfm.similar_tracks(artist="Artist", title="Track")
        similar_artists = await lastfm.similar_artists("Artist")
        tags = await lastfm.track_tags(artist="Artist", title="Track")

    assert route.call_count == 6
    assert artists == ["Artist"]
    assert top_tracks[0].duration_ms == 180_000
    assert top_tracks[0].provenance["lastfm_url"] == "https://last.fm/track"
    assert recent[0].artists == ["Artist"]
    assert similar[0].source_confidence == 0.75
    assert similar_artists == ["Other"]
    assert tags == ["chill"]


@respx.mock
async def test_client_retries_transient_errors_and_normalizes_rate_limits() -> None:
    """Safe reads retry transient failure and surface Last.fm API rate limits clearly."""
    route = respx.get("https://lastfm.example/2.0/").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"topartists": {"artist": [{"name": "Artist"}]}}),
            httpx.Response(200, json={"error": 29, "message": "Rate limit exceeded"}),
            httpx.Response(200, json={"error": 29, "message": "Rate limit exceeded"}),
            httpx.Response(200, json={"error": 29, "message": "Rate limit exceeded"}),
        ]
    )
    sleeps: list[float] = []
    async with httpx.AsyncClient() as http_client:
        lastfm = client(http_client, sleeps)
        assert await lastfm.user_top_artists() == ["Artist"]
        with pytest.raises(LastfmRateLimitError):
            await lastfm.user_top_artists()

    assert route.call_count == 5
    assert sleeps == [1.0, 1.0, 2.0]


@respx.mock
async def test_client_marks_missing_user_profile_as_optional_source_failure() -> None:
    """A missing or private profile does not become an opaque malformed-response error."""
    respx.get("https://lastfm.example/2.0/").mock(
        return_value=httpx.Response(200, json={"error": 6, "message": "User not found"})
    )
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(LastfmProfileError):
            await client(http_client, []).user_recent_tracks()


@respx.mock
async def test_long_retry_after_falls_back_without_retrying_early() -> None:
    """A long rate limit must not stall the preview or trigger an early retry."""
    route = respx.get("https://lastfm.example/2.0/").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "60"})
    )
    sleeps: list[float] = []
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(LastfmRateLimitError):
            await client(http_client, sleeps).user_top_tracks()
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
async def test_client_caps_unexpectedly_large_responses() -> None:
    """An API response exceeding the requested limit cannot grow cached history unbounded."""
    respx.get("https://lastfm.example/2.0/").mock(
        return_value=httpx.Response(
            200, json={"topartists": {"artist": [{"name": "First"}, {"name": "Second"}]}}
        )
    )
    async with httpx.AsyncClient() as http_client:
        assert await client(http_client, []).user_top_artists(limit=1) == ["First"]
