"""Tests for conservative Last.fm-to-Spotify candidate resolution."""

from collections.abc import Sequence
from typing import cast

import pytest

from tests.factories import candidate
from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.domain.protocols import SpotifyGateway
from tunetrail.recommendation.resolver import SpotifyCandidateResolver


class FakeSpotify:
    """Return programmed Spotify search results while retaining the resolver query."""

    def __init__(self, results: Sequence[Candidate]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Return configured candidate results for resolution coverage."""
        self.queries.append(query)
        assert limit == 10
        return self.results


def lastfm_candidate(**changes: object) -> Candidate:
    """Build one external Last.fm suggestion with source-specific provenance."""
    values: dict[str, object] = {
        "source": CandidateSource.LASTFM,
        "source_id": "lastfm-track",
        "title": "Love Story",
        "artists": ["Taylor Swift"],
        "duration_ms": 235_000,
        "source_confidence": 0.8,
        "provenance": {"lastfm_url": "https://last.fm/track"},
    }
    values.update(changes)
    return Candidate.model_validate(values)


async def test_resolver_preserves_lastfm_evidence_after_high_confidence_spotify_match() -> None:
    """A valid exact title/artist match gains a Spotify URI without losing Last.fm provenance."""
    spotify_result = candidate(
        source_id="spotify-track",
        title="Love Story",
        artists=["Taylor Swift"],
        duration_ms=235_001,
        explicit=False,
        provenance={"spotify_uri": "spotify:track:spotifytrack"},
    )
    spotify = FakeSpotify([spotify_result])
    resolver = SpotifyCandidateResolver(cast(SpotifyGateway, spotify))

    resolved = await resolver.resolve(lastfm_candidate())

    assert resolved is not None
    assert resolved.source is CandidateSource.LASTFM
    assert resolved.source_id == "spotify-track"
    assert resolved.provenance["lastfm_url"] == "https://last.fm/track"
    assert resolved.provenance["spotify_uri"] == "spotify:track:spotifytrack"
    assert resolved.provenance["spotify_match_confidence"] == "1.00"
    assert spotify.queries == ['artist:"Taylor Swift" track:"Love Story"']


async def test_resolver_discards_ambiguous_or_low_confidence_spotify_results() -> None:
    """Artist/title disagreement never creates a playable URI from an external suggestion."""
    spotify = FakeSpotify(
        [
            candidate(
                source_id="wrong",
                title="Different Song",
                artists=["Different Artist"],
                duration_ms=235_000,
                explicit=False,
                provenance={"spotify_uri": "spotify:track:wrong"},
            )
        ]
    )

    assert (
        await SpotifyCandidateResolver(cast(SpotifyGateway, spotify)).resolve(lastfm_candidate())
        is None
    )


@pytest.mark.parametrize(
    ("title", "artist", "duration"),
    [
        ("Love Story Live", "Taylor Swift", 235_000),
        ("Love Story", "Taylor Swift Tribute Band", 235_000),
        ("Love Story", "Taylor Swift", 300_000),
    ],
)
async def test_resolver_rejects_ambiguous_editions_artists_and_durations(
    title: str, artist: str, duration: int
) -> None:
    """Superstrings and contradictory duration evidence are not sufficient matches."""
    result = candidate(
        title=title,
        artists=[artist],
        duration_ms=duration,
        provenance={"spotify_uri": "spotify:track:wrong"},
    )
    resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify([result])))
    assert await resolver.resolve(lastfm_candidate()) is None


async def test_resolver_matches_non_ascii_names_without_collapsing_them() -> None:
    """Names in non-Latin scripts remain distinguishable and can match exactly."""
    result = candidate(
        title="夜に駆ける",
        artists=["東京"],
        provenance={"spotify_uri": "spotify:track:unicode"},
    )
    resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify([result])))
    assert await resolver.resolve(lastfm_candidate(title="別の曲", artists=["東京"])) is None
    assert (
        await resolver.resolve(lastfm_candidate(title="夜に駆ける", artists=["東京"])) is not None
    )


async def test_resolver_rejects_equally_supported_distinct_spotify_tracks() -> None:
    """Identical metadata on different URIs cannot establish which edition is correct."""
    results = [
        candidate(
            title="Love Story",
            artists=["Taylor Swift"],
            duration_ms=235_000,
            source_id=identifier,
            provenance={"spotify_uri": f"spotify:track:{identifier}"},
        )
        for identifier in ("original", "alternative")
    ]
    resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify(results)))
    assert await resolver.resolve(lastfm_candidate()) is None
