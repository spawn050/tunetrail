"""Tests for cache-aware optional Last.fm expansion and Spotify-only failure fallback."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from tests.factories import candidate, intent
from tunetrail.domain.models import Candidate, CandidateSource, LastfmProfile, ProfileSummary
from tunetrail.domain.protocols import LastfmGateway, PersonalizationStore, SpotifyGateway
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore
from tunetrail.recommendation.lastfm import LastfmEnrichmentService
from tunetrail.recommendation.resolver import SpotifyCandidateResolver


class FakeStore:
    """Retain fresh normalized Last.fm values in memory for enrichment behavior tests."""

    def __init__(self) -> None:
        self.profile: LastfmProfile | None = None
        self.candidates: dict[str, list[Candidate]] = {}

    async def lastfm_profile(self, cache_key: str, *, now: datetime) -> LastfmProfile | None:
        """Return the current profile regardless of opaque key in this focused fake."""
        del cache_key, now
        return self.profile

    async def save_lastfm_profile(
        self,
        cache_key: str,
        profile: LastfmProfile,
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Capture the normalized profile rather than any raw Last.fm data."""
        del cache_key, refreshed_at, expires_at
        self.profile = profile

    async def lastfm_candidates(
        self, cache_key: str, *, now: datetime
    ) -> Sequence[Candidate] | None:
        """Return cached candidate expansions by opaque cache key."""
        del now
        return self.candidates.get(cache_key)

    async def save_lastfm_candidates(
        self,
        cache_key: str,
        candidates: Sequence[Candidate],
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Capture the bounded normalized expansion candidate values."""
        del refreshed_at, expires_at
        self.candidates[cache_key] = list(candidates)


class FakeLastfm:
    """Return predictable history, similarity, and community tag signals."""

    def __init__(self) -> None:
        self.profile_reads = 0

    async def user_top_artists(self, *, limit: int = 20) -> Sequence[str]:
        """Return one Last.fm artist history signal."""
        self.profile_reads += 1
        return ["Seed Artist"]

    async def user_top_tracks(self, *, limit: int = 20) -> Sequence[Candidate]:
        """Return one Last.fm track history signal."""
        self.profile_reads += 1
        return [lastfm_candidate(title="Seed Track", artists=["Seed Artist"])]

    async def user_recent_tracks(self, *, limit: int = 20) -> Sequence[Candidate]:
        """Return no recents while exercising the complete profile import boundary."""
        self.profile_reads += 1
        return []

    async def similar_tracks(
        self, *, artist: str, title: str, limit: int = 10
    ) -> Sequence[Candidate]:
        """Return one community-similar track for the profile track seed."""
        assert (artist, title, limit) == ("Seed Artist", "Seed Track", 4)
        return [lastfm_candidate(title="Similar Track", artists=["Similar Artist"])]

    async def similar_artists(self, artist: str, *, limit: int = 5) -> Sequence[str]:
        """Return no artist expansion so this test targets track expansion and tag handling."""
        assert artist == "Seed Artist"
        assert limit == 2
        return []

    async def artist_top_tracks(self, artist: str, *, limit: int = 3) -> Sequence[Candidate]:
        """This focused path has no similar artists to expand."""
        raise AssertionError(f"unexpected artist expansion for {artist}")

    async def track_tags(self, *, artist: str, title: str, limit: int = 5) -> Sequence[str]:
        """Return duplicate mixed-case tags to prove deterministic normalization."""
        assert (artist, title, limit) == ("Similar Artist", "Similar Track", 5)
        return ["Chill", "chill", "Study"]

    async def artist_tags(self, artist: str, *, limit: int = 5) -> Sequence[str]:
        """This focused path has no similar artists to tag."""
        raise AssertionError(f"unexpected artist tags for {artist}")


class FakeSpotify:
    """Resolve one exact external title/artist candidate to a Spotify track URI."""

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Return the corresponding Spotify candidate from a conservative resolver query."""
        assert query == 'artist:"Similar Artist" track:"Similar Track"'
        assert limit == 10
        return [
            candidate(
                source_id="spotifysimilar",
                title="Similar Track",
                artists=["Similar Artist"],
                duration_ms=180_000,
                explicit=False,
                provenance={"spotify_uri": "spotify:track:spotifysimilar"},
            )
        ]


def lastfm_candidate(**changes: object) -> Candidate:
    """Create a Last.fm suggestion before it is conservatively resolved through Spotify."""
    values: dict[str, object] = {
        "source": CandidateSource.LASTFM,
        "source_id": "lastfm-candidate",
        "title": "Track",
        "artists": ["Artist"],
        "source_confidence": 0.8,
    }
    values.update(changes)
    return Candidate.model_validate(values)


async def test_enrichment_imports_caches_tags_and_resolves_lastfm_suggestions() -> None:
    """Only high-confidence Spotify-mapped candidates survive optional Last.fm enrichment."""
    store = FakeStore()
    lastfm = FakeLastfm()
    service = LastfmEnrichmentService(
        lastfm=cast(LastfmGateway, lastfm),
        resolver=SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify())),
        store=cast(PersonalizationStore, store),
        username="listener",
    )

    profile = ProfileSummary(updated_at=datetime.now(UTC))
    candidates = await service.find(intent(moods=["chill"]), profile)
    repeated = await service.find(intent(moods=["chill"]), profile)

    assert lastfm.profile_reads == 3
    assert [candidate.tags for candidate in candidates] == [["chill", "study"]]
    assert candidates[0].provenance["spotify_uri"] == "spotify:track:spotifysimilar"
    assert repeated == candidates


async def test_recent_tracks_supply_a_bounded_distinct_similarity_seed() -> None:
    """Recent listening contributes even when top-track history fills the seed budget."""
    store = FakeStore()
    store.profile = LastfmProfile(
        top_tracks=[lastfm_candidate(title=f"Top {index}") for index in range(4)],
        recent_tracks=[lastfm_candidate(title="Recent"), lastfm_candidate(title="Top 0")],
        updated_at=datetime.now(UTC),
    )
    seeds: list[str] = []

    class RecordingLastfm(FakeLastfm):
        async def similar_tracks(
            self, *, artist: str, title: str, limit: int = 10
        ) -> Sequence[Candidate]:
            seeds.append(title)
            return []

    service = LastfmEnrichmentService(
        lastfm=cast(LastfmGateway, RecordingLastfm()),
        resolver=SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify())),
        store=cast(PersonalizationStore, store),
        username="listener",
    )
    assert await service.find(intent(), None) == []
    assert seeds == ["Top 0", "Top 1", "Recent"]


async def test_profile_cache_is_isolated_by_username(tmp_path: Path) -> None:
    """Switching Last.fm accounts cannot reuse another account's cached listening profile."""
    store = SQLitePersonalizationStore(tmp_path / "profile.sqlite3")
    first, second = FakeLastfm(), FakeLastfm()
    resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify()))
    first_service = LastfmEnrichmentService(
        lastfm=cast(LastfmGateway, first), resolver=resolver, store=store, username="alice"
    )
    second_service = LastfmEnrichmentService(
        lastfm=cast(LastfmGateway, second), resolver=resolver, store=store, username="bob"
    )
    await first_service.find(intent(), None)
    await second_service.find(intent(), None)
    assert first.profile_reads == second.profile_reads == 3
