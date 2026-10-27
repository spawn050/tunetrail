"""Tests for profile-seeded Spotify candidate retrieval."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

import pytest

from tests.factories import candidate, intent
from tunetrail.domain.models import Candidate, ProfileSummary
from tunetrail.domain.protocols import SpotifyGateway
from tunetrail.recommendation.ranking import PersonalizationClarification
from tunetrail.recommendation.retrieval import SpotifyProfileCandidateRetriever


class FakeSpotify:
    """Capture constrained Spotify search queries without network calls."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Return one unique candidate for every profile-derived query."""
        self.queries.append(query)
        return [candidate(source_id=str(len(self.queries)))]


def profile() -> ProfileSummary:
    """Build a compact local profile with enough seeds for retrieval tests."""
    return ProfileSummary(
        favorite_artists=["Artist One", "Artist Two"],
        updated_at=datetime.now(UTC),
    )


async def test_vague_retrieval_uses_profile_artist_seeds_not_mood_playlist_search() -> None:
    """Mood text never becomes a generic Spotify playlist or keyword fallback."""
    spotify = FakeSpotify()
    retriever = SpotifyProfileCandidateRetriever(cast(SpotifyGateway, spotify))

    results = await retriever.find(intent(moods=["peaceful"], activities=["studying"]), profile())

    assert [item.source_id for item in results] == ["1", "2"]
    assert spotify.queries == ['artist:"Artist One"', 'artist:"Artist Two"']


async def test_direct_constraints_create_one_safe_field_filtered_spotify_query() -> None:
    """Specific artist, track, and year terms remain deterministic Spotify query fields."""
    spotify = FakeSpotify()
    retriever = SpotifyProfileCandidateRetriever(cast(SpotifyGateway, spotify))

    await retriever.find(
        intent(artists=["Artist"], track_terms=["Track"], year_start=2010, year_end=2012), None
    )

    assert spotify.queries == ['artist:"Artist" track:"Track" year:2010-2012']


async def test_empty_profile_requires_refresh_instead_of_generic_catalog_discovery() -> None:
    """No local taste seed means TuneTrail declines vague personalized search safely."""
    with pytest.raises(PersonalizationClarification, match="no artist seeds"):
        await SpotifyProfileCandidateRetriever(cast(SpotifyGateway, FakeSpotify())).find(
            intent(moods=["peaceful"]),
            ProfileSummary(updated_at=datetime.now(UTC)),
        )
