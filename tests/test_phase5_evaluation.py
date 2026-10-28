"""Fixed synthetic evaluation; counts are regression evidence, not accuracy estimates."""

from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from tunetrail.domain.models import (
    Candidate,
    CandidateSource,
    ExplicitPreference,
    ListeningIntent,
    PlaybackAction,
    PreferenceSource,
    PreferenceSubject,
)
from tunetrail.musicbrainz.client import MusicBrainzError
from tunetrail.musicbrainz.models import Artist, Recording
from tunetrail.recommendation.independent_discovery import (
    EnrichedCandidate,
    IndependentDiscovery,
    IndependentSeed,
    SeedKind,
)
from tunetrail.recommendation.ranking import rank_candidates
from tunetrail.recommendation.spotify_preview import build_preview
from tunetrail.spotify.approval import PlayerApprovals


class SyntheticSource:
    async def recording(self, recording_id: UUID) -> Recording:
        number = recording_id.int
        if number == 5:
            raise MusicBrainzError("synthetic outage")
        return Recording(
            id=str(recording_id),
            title=f"Song {number}",
            duration_ms=180000,
            artists=[Artist(id=str(UUID(int=100 + number)), name=f"Artist {number}")],
        )

    async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
        raise AssertionError("recording-only synthetic evaluation")


class SyntheticSpotify:
    async def search_playable_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        number = next(i for i in range(1, 5) if f'Song {i}"' in query)
        if number == 4:
            raise TimeoutError("synthetic outage")

        def candidate(identifier: str, title: str) -> Candidate:
            return Candidate(
                source=CandidateSource.SPOTIFY,
                source_id=identifier,
                title=title,
                artists=[f"Artist {number}"],
                duration_ms=180000,
                provenance={"spotify_uri": f"spotify:track:{identifier}"},
            )

        if number == 2:
            return [candidate("a", "Song 2"), candidate("b", "Song 2")]
        if number == 3:
            return [candidate("c", "Song 3 (Live)")]
        return [candidate("d", "Song 1")]


async def test_synthetic_discovery_ranking_and_preview_metrics() -> None:
    """Five requested seeds: success, ambiguous, mismatch, search outage, source outage."""
    seeds = [IndependentSeed(SeedKind.RECORDING, UUID(int=i)) for i in range(1, 6)]
    discovered = await IndependentDiscovery(SyntheticSource()).discover(seeds, limit=5)
    assert len(discovered.candidates) == 4
    assert discovered.source_failures == 1 and discovered.needs_more_seeds
    preferences = [
        ExplicitPreference(
            subject=subject,
            name=name,
            strength=strength,
            source=PreferenceSource.DIRECT,
            updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        for subject, name, strength in (
            (PreferenceSubject.ARTIST, "Artist 1", 2),
            (PreferenceSubject.ARTIST, "Artist 2", -1),
            (PreferenceSubject.FEATURE, "energy", 1),
        )
    ]
    ranks = rank_candidates(
        [EnrichedCandidate(c, None, "unavailable") for c in discovered.candidates],
        preferences,
        ListeningIntent(queue_size=5),
    )
    assert [c.item.candidate.recording_id.int for c in ranks.candidates] == [1, 3, 4, 2]
    assert ranks.candidates[0].signals == (("artist preference: Artist 1", 2.0),)
    assert ranks.candidates[-1].signals == (("artist preference: Artist 2", -1.0),)
    assert all("energy" not in signal for c in ranks.candidates for signal, _ in c.signals)
    assert all(c.score == sum(value for _, value in c.signals) for c in ranks.candidates)

    start = perf_counter()
    preview = await build_preview(
        ranks, SyntheticSpotify(), PlayerApprovals(), action=PlaybackAction.PLAY_NOW
    )
    elapsed_ms = (perf_counter() - start) * 1000
    assert len(preview.matches) == 1 and preview.matches[0].title == "Song 1"
    assert preview.proposal_id and "Exact normalized" in preview.matches[0].evidence
    assert any("Song 2: no unique" in issue for issue in preview.issues)
    assert any("Song 3: no unique" in issue for issue in preview.issues)
    assert any("Song 4: Spotify search unavailable" in issue for issue in preview.issues)
    distinct_artists = len({c.item.candidate.artists[0] for c in ranks.candidates})
    print(
        "\nSynthetic evaluation (not real-world accuracy): "
        f"discovery={len(discovered.candidates)}/{len(seeds)}; "
        f"discovery failures={discovered.source_failures}/{len(seeds)}; "
        "known ambiguous search=1/4; search failures=1/4; "
        f"resolved={len(preview.matches)}/{len(ranks.candidates)}; "
        f"distinct primary artists={distinct_artists}/{len(ranks.candidates)}; "
        f"mocked preview latency={elapsed_ms:.3f} ms"
    )
