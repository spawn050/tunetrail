"""Tests for the approval-gated natural-language LangGraph workflow."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

import pytest

from tests.factories import candidate, feedback, intent
from tunetrail.domain.models import (
    ActionHistoryEntry,
    Candidate,
    CandidateSource,
    Feedback,
    ListeningIntent,
    ProfileSummary,
)
from tunetrail.domain.protocols import (
    CandidateSourceProvider,
    PersonalizationStore,
    SpotifyGateway,
)
from tunetrail.graph.models import ApprovalDecision, RequestOutcome
from tunetrail.graph.request_graph import NaturalLanguageRequestGraph
from tunetrail.recommendation.resolver import SpotifyCandidateResolver


class FakeParser:
    """Return a fixed parsed intent without a network-capable LLM."""

    def __init__(self, parsed: ListeningIntent) -> None:
        self.parsed = parsed

    async def parse(self, request: str) -> ListeningIntent:
        """Confirm the graph forwards the listener request to its parser."""
        assert request
        return self.parsed


class FakeSpotify:
    """Track reads and writes so tests prove the approval boundary."""

    def __init__(self, candidates: Sequence[Candidate]) -> None:
        self.candidates = candidates
        self.searches: list[str] = []
        self.started: list[list[str]] = []
        self.appended: list[str] = []

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Return programmed Spotify candidates for deterministic resolution."""
        self.searches.append(query)
        assert limit <= 10
        return self.candidates

    async def start_playback(
        self, spotify_uris: Sequence[str], *, device_id: str | None = None
    ) -> None:
        """Record graph-approved playback calls."""
        assert device_id == "device-1"
        self.started.append(list(spotify_uris))

    async def add_to_queue(self, spotify_uri: str, *, device_id: str | None = None) -> None:
        """Record graph-approved append calls."""
        assert device_id == "device-1"
        self.appended.append(spotify_uri)


class FakePersonalizationStore:
    """Provide compact local signals and capture only approval-completed session writes."""

    def __init__(self) -> None:
        self.recorded: list[ActionHistoryEntry] = []

    async def get(self) -> ProfileSummary | None:
        """Return an existing local profile suitable for vague Spotify-only requests."""
        return ProfileSummary(favorite_artists=["Taylor Swift"], updated_at=datetime.now(UTC))

    async def recent(self) -> Sequence[Feedback]:
        """Return one harmless feedback item to exercise the profile-loading path."""
        return [feedback(spotify_uri="spotify:track:notpresent")]

    async def recent_track_uris(self) -> Sequence[str]:
        """Return no generated-session repetition penalties for this focused test."""
        return []

    async def record_action(
        self, entry: ActionHistoryEntry, *, score_json: Sequence[str | None]
    ) -> None:
        """Record only actions reached via the approved graph route."""
        assert len(score_json) == len(entry.spotify_uris)
        self.recorded.append(entry)


class FakeLastfmEnrichment:
    """Supply a resolved external suggestion without exposing an external API to graph tests."""

    async def find(
        self, parsed: ListeningIntent, profile: ProfileSummary | None
    ) -> Sequence[Candidate]:
        """Return one Last.fm-tagged candidate using the existing Spotify URI shape."""
        assert parsed.moods == ["peaceful"]
        assert profile is not None
        return [
            playable_candidate(
                source="lastfm",
                source_id="lastfm-track",
                tags=["peaceful"],
                provenance={
                    "spotify_uri": "spotify:track:lastfmtrack",
                    "lastfm_url": "https://last.fm/track",
                },
            )
        ]


class UnavailableLastfmEnrichment:
    """Simulate an unexpected optional-source outage at the graph boundary."""

    async def find(
        self, parsed: ListeningIntent, profile: ProfileSummary | None
    ) -> Sequence[Candidate]:
        """Fail without making any Spotify player calls."""
        raise RuntimeError("Last.fm unavailable")


def playable_candidate(**changes: object) -> Candidate:
    """Build a playable direct Spotify search result."""
    values: dict[str, object] = {
        "source_id": "track-1",
        "title": "Love Story",
        "artists": ["Taylor Swift"],
        "duration_ms": 235_000,
        "explicit": False,
        "year": 2008,
        "provenance": {"spotify_uri": "spotify:track:track1"},
    }
    values.update(changes)
    return candidate(**values)


async def test_graph_interrupts_before_approved_playback() -> None:
    """Preview construction performs a Spotify read but no player mutation."""
    spotify = FakeSpotify([playable_candidate()])
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(artists=["Taylor Swift"], year_start=2008, year_end=2012)),
        spotify=cast(SpotifyGateway, spotify),
    )

    result = await graph.start("Play Taylor Swift from 2008 to 2012", device_id="device-1")

    assert result.outcome is RequestOutcome.PREVIEW_READY
    assert result.preview is not None
    assert spotify.started == []


async def test_graph_lastfm_outage_preserves_spotify_preview_and_approval() -> None:
    """An enrichment failure falls back to Spotify and cancellation never mutates playback."""
    spotify = FakeSpotify([playable_candidate()])
    store = FakePersonalizationStore()
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(moods=["peaceful"])),
        spotify=cast(SpotifyGateway, spotify),
        store=cast(PersonalizationStore, store),
        lastfm_enrichment=cast(CandidateSourceProvider, UnavailableLastfmEnrichment()),
    )

    result = await graph.start("Play something peaceful", device_id="device-1")

    assert result.outcome is RequestOutcome.PREVIEW_READY
    assert result.preview is not None
    assert [item.track.spotify_uri for item in result.preview.items] == ["spotify:track:track1"]
    assert not spotify.started and not spotify.appended and not store.recorded
    assert (await graph.resume(ApprovalDecision.CANCEL)).outcome is RequestOutcome.CANCELLED
    assert not spotify.started and not spotify.appended and not store.recorded
    assert spotify.appended == []


async def test_graph_approval_resumes_to_the_only_mutating_node() -> None:
    """Approve is the only decision that invokes the Spotify player gateway."""
    spotify = FakeSpotify([playable_candidate()])
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(artists=["Taylor Swift"])), spotify=cast(SpotifyGateway, spotify)
    )

    await graph.start("Play Taylor Swift", device_id="device-1")
    result = await graph.resume(ApprovalDecision.APPROVE)

    assert result.outcome is RequestOutcome.COMPLETED
    assert spotify.started == [["spotify:track:track1"]]


async def test_graph_cancellation_and_edit_never_mutate_spotify() -> None:
    """Cancellation and edit are terminal safe exits from an interrupted graph."""
    spotify = FakeSpotify([playable_candidate()])
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(artists=["Taylor Swift"])), spotify=cast(SpotifyGateway, spotify)
    )

    await graph.start("Play Taylor Swift", device_id="device-1")
    cancelled = await graph.resume(ApprovalDecision.CANCEL)
    await graph.start("Play Taylor Swift", device_id="device-1")
    edited = await graph.resume(ApprovalDecision.EDIT)

    assert cancelled.outcome is RequestOutcome.CANCELLED
    assert edited.outcome is RequestOutcome.EDIT_REQUESTED
    assert spotify.started == []
    assert spotify.appended == []


async def test_graph_returns_clarification_before_spotify_for_vague_requests() -> None:
    """Vague personalization is deferred instead of becoming a generic catalog query."""
    spotify = FakeSpotify([playable_candidate()])
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(moods=["peaceful"])), spotify=cast(SpotifyGateway, spotify)
    )

    result = await graph.start("Play something peaceful", device_id="device-1")

    assert result.outcome is RequestOutcome.CLARIFICATION_NEEDED
    assert result.clarification is not None
    assert spotify.searches == []


async def test_graph_regeneration_retrieves_again_without_mutating_spotify() -> None:
    """Regeneration creates another preview and remains upstream of the approval route."""
    spotify = FakeSpotify([playable_candidate()])
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(artists=["Taylor Swift"])), spotify=cast(SpotifyGateway, spotify)
    )

    await graph.start("Play Taylor Swift", device_id="device-1")
    result = await graph.resume(ApprovalDecision.REGENERATE)

    assert result.outcome is RequestOutcome.PREVIEW_READY
    assert len(spotify.searches) == 2
    assert spotify.started == []


async def test_graph_personalizes_vague_request_then_persists_only_after_approval() -> None:
    """Profile-seeded requests remain approval-gated and create URI-only sessions afterward."""
    spotify = FakeSpotify([playable_candidate()])
    store = FakePersonalizationStore()
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(moods=["peaceful"], activities=["studying"])),
        spotify=cast(SpotifyGateway, spotify),
        store=cast(PersonalizationStore, store),
    )

    preview = await graph.start("Play something peaceful for studying", device_id="device-1")

    assert preview.outcome is RequestOutcome.PREVIEW_READY
    assert spotify.searches == ['artist:"Taylor Swift"']
    assert store.recorded == []
    completed = await graph.resume(ApprovalDecision.APPROVE)

    assert completed.outcome is RequestOutcome.COMPLETED
    assert [entry.spotify_uris for entry in store.recorded] == [["spotify:track:track1"]]


async def test_graph_merges_lastfm_enrichment_before_the_same_approval_interrupt() -> None:
    """External suggestions are Spotify-resolved and previewed, never played before approval."""
    spotify = FakeSpotify([playable_candidate()])
    store = FakePersonalizationStore()
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(moods=["peaceful"])),
        spotify=cast(SpotifyGateway, spotify),
        store=cast(PersonalizationStore, store),
        lastfm_enrichment=cast(CandidateSourceProvider, FakeLastfmEnrichment()),
    )

    preview = await graph.start("Play something peaceful", device_id="device-1")

    assert preview.outcome is RequestOutcome.PREVIEW_READY
    assert preview.preview is not None
    assert any(
        item.track.spotify_uri == "spotify:track:lastfmtrack" for item in preview.preview.items
    )
    assert spotify.started == []


@pytest.mark.parametrize(
    ("parsed", "favorite_artist"),
    [
        (
            intent(moods=["peaceful"], activities=["studying"], explicit_allowed=False),
            "Taylor Swift",
        ),
        (intent(familiarity="discovery", explicit_allowed=False), "Another Artist"),
        (intent(familiarity="familiar", explicit_allowed=False), "Taylor Swift"),
    ],
)
async def test_comparative_graph_previews_keep_filters_and_approval(
    parsed: ListeningIntent, favorite_artist: str
) -> None:
    """Compare real graph merge/rank paths with resolved, duplicate and invalid suggestions."""

    class Store(FakePersonalizationStore):
        async def get(self) -> ProfileSummary:
            return ProfileSummary(favorite_artists=[favorite_artist], updated_at=datetime.now(UTC))

    class Enrichment:
        async def find(
            self, parsed: ListeningIntent, profile: ProfileSummary | None
        ) -> Sequence[Candidate]:
            suggestions = [
                (playable_candidate(), playable_candidate()),
                (
                    playable_candidate(title="New Song", artists=["New Artist"]),
                    playable_candidate(
                        source_id="new",
                        title="New Song",
                        artists=["New Artist"],
                        provenance={"spotify_uri": "spotify:track:new"},
                    ),
                ),
                (
                    playable_candidate(title="Explicit", artists=["Third Artist"]),
                    playable_candidate(
                        source_id="explicit",
                        title="Explicit",
                        artists=["Third Artist"],
                        explicit=True,
                        provenance={"spotify_uri": "spotify:track:explicit"},
                    ),
                ),
                (
                    playable_candidate(title="Edition", artists=["Fourth Artist"]),
                    playable_candidate(
                        source_id="wrong",
                        title="Edition Live",
                        artists=["Fourth Artist"],
                        provenance={"spotify_uri": "spotify:track:wrong"},
                    ),
                ),
                (
                    playable_candidate(title="Incomplete", artists=["Fifth Artist"]),
                    playable_candidate(
                        source_id="incomplete",
                        title="Incomplete",
                        artists=["Fifth Artist"],
                        duration_ms=None,
                        provenance={"spotify_uri": "spotify:track:incomplete"},
                    ),
                ),
            ]
            resolved: list[Candidate] = []
            for external, result in suggestions:
                external = external.model_copy(
                    update={"source": CandidateSource.LASTFM, "tags": ["peaceful"]}
                )
                resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FakeSpotify([result])))
                match = await resolver.resolve(external)
                if match is not None:
                    resolved.append(match)
            return resolved

    async def preview(enriched: bool) -> tuple[list[str], set[str]]:
        spotify = FakeSpotify([playable_candidate()])
        store = Store()
        graph = NaturalLanguageRequestGraph(
            parser=FakeParser(parsed),
            spotify=cast(SpotifyGateway, spotify),
            store=cast(PersonalizationStore, store),
            lastfm_enrichment=cast(CandidateSourceProvider, Enrichment()) if enriched else None,
        )
        result = await graph.start("Compare preview", device_id="device-1")
        assert result.outcome is RequestOutcome.PREVIEW_READY and result.preview is not None
        uris = [item.track.spotify_uri for item in result.preview.items]
        artists = {artist for item in result.preview.items for artist in item.track.artists}
        assert not spotify.started and not spotify.appended and not store.recorded
        assert (await graph.resume(ApprovalDecision.CANCEL)).outcome is RequestOutcome.CANCELLED
        assert not spotify.started and not spotify.appended and not store.recorded
        return uris, artists

    baseline_uris, baseline_artists = await preview(False)
    enriched_uris, enriched_artists = await preview(True)
    assert baseline_uris == ["spotify:track:track1"]
    assert set(enriched_uris) == {"spotify:track:track1", "spotify:track:new"}
    assert len(enriched_uris) == 2
    assert len(baseline_artists) == 1 and len(enriched_artists) == 2
