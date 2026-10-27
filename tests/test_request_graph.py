"""Tests for the approval-gated natural-language LangGraph workflow."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from tests.factories import candidate, feedback, intent
from tunetrail.domain.models import (
    ActionHistoryEntry,
    Candidate,
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
