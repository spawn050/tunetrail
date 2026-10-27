"""Tests for application composition and the Textual shell."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from textual.widgets import Button, Input, Select, Static

from tests.factories import intent, resolved_track
from tunetrail.domain.models import (
    ActionHistoryEntry,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    Feedback,
    FeedbackKind,
    ListeningIntent,
    PlaybackAction,
    PlaybackState,
    PlaylistSignal,
    ProfileSummary,
    QueueItem,
    QueuePreview,
    ResolvedTrack,
)
from tunetrail.domain.protocols import PersonalizationStore, ProfileRefresher, RequestGraph
from tunetrail.graph.models import ApprovalDecision, RequestGraphResult, RequestOutcome
from tunetrail.spotify.client import SpotifyClient
from tunetrail.spotify.models import SpotifyUser
from tunetrail.tui.app import (
    CatalogSearchCommandError,
    CommandInput,
    SpotifyControlApp,
    parse_catalog_search_command,
)


class FakeSpotify:
    """In-memory Spotify substitute for non-mutating and confirmed TUI flows."""

    def __init__(self) -> None:
        self.track = resolved_track()
        self.catalog_item = CatalogItem(
            kind=CatalogItemKind.TRACK,
            spotify_id=self.track.spotify_id,
            spotify_uri=self.track.spotify_uri,
            title=self.track.title,
            subtitle="Artist",
            track=self.track,
        )
        self.started: list[list[str]] = []
        self.appended: list[str] = []

    async def current_user(self) -> SpotifyUser:
        """Return a minimal authenticated profile."""
        return SpotifyUser(id="listener")

    async def devices(self) -> Sequence[Device]:
        """Return one active device."""
        return [Device(id="device-1", name="Desktop", kind="Computer", is_active=True)]

    async def current_playback(self) -> PlaybackState:
        """Return current playback for status rendering."""
        return PlaybackState(
            device=(await self.devices())[0],
            track=self.track,
            is_playing=True,
            progress_ms=1_000,
            fetched_at=datetime.now(UTC),
        )

    async def current_queue(self) -> CurrentQueue:
        """Return an empty visible queue."""
        return CurrentQueue()

    async def search_catalog(
        self, query: str, *, kind: CatalogItemKind, limit: int = 10
    ) -> Sequence[CatalogItem]:
        """Return one typed result for an explicit track search."""
        assert query == "Track"
        assert kind is CatalogItemKind.TRACK
        assert limit == 10
        return [self.catalog_item]

    async def tracks_for_catalog_item(
        self, item: CatalogItem, *, limit: int = 10
    ) -> Sequence[ResolvedTrack]:
        """Return the selected track for local preview construction."""
        assert item == self.catalog_item
        assert limit == 10
        return [self.track]

    async def user_playlists(self) -> Sequence[CatalogItem]:
        """Return one eligible playlist for explicit local taste-signal opt-in."""
        return [
            CatalogItem(
                kind=CatalogItemKind.PLAYLIST,
                spotify_id="playlist-1",
                spotify_uri="spotify:playlist:playlist-1",
                title="Taste signals",
            )
        ]

    async def start_playback(
        self, spotify_uris: Sequence[str], *, device_id: str | None = None
    ) -> None:
        """Record confirmed play-now calls."""
        assert device_id == "device-1"
        self.started.append(list(spotify_uris))

    async def add_to_queue(self, spotify_uri: str, *, device_id: str | None = None) -> None:
        """Record confirmed append calls."""
        assert device_id == "device-1"
        self.appended.append(spotify_uri)


class FakeRequestGraph:
    """Return deterministic approval states without an LLM or LangGraph runtime."""

    def __init__(self, track: ResolvedTrack) -> None:
        self.preview = QueuePreview(
            intent=resolved_intent(),
            items=[QueueItem(track=track, explanation="Spotify result matching artist: Artist.")],
            created_at=datetime.now(UTC),
        )
        self.requests: list[str] = []
        self.decisions: list[ApprovalDecision] = []

    async def start(self, request: str, *, device_id: str | None = None) -> RequestGraphResult:
        """Expose a preview without any player mutation."""
        assert device_id == "device-1"
        self.requests.append(request)
        return RequestGraphResult(outcome=RequestOutcome.PREVIEW_READY, preview=self.preview)

    async def resume(self, decision: ApprovalDecision) -> RequestGraphResult:
        """Return terminal or refreshed outcomes for explicit decisions."""
        self.decisions.append(decision)
        if decision is ApprovalDecision.APPROVE:
            return RequestGraphResult(outcome=RequestOutcome.COMPLETED, preview=self.preview)
        if decision is ApprovalDecision.REGENERATE:
            return RequestGraphResult(outcome=RequestOutcome.PREVIEW_READY, preview=self.preview)
        if decision is ApprovalDecision.EDIT:
            return RequestGraphResult(outcome=RequestOutcome.EDIT_REQUESTED)
        return RequestGraphResult(outcome=RequestOutcome.CANCELLED)


class FakePersonalizationStore:
    """In-memory local profile store for TUI feedback and profile-control coverage."""

    def __init__(self) -> None:
        self.signals: list[PlaylistSignal] = []
        self.feedback: list[Feedback] = []
        self.actions: list[ActionHistoryEntry] = []
        self.was_reset = False

    async def playlist_signals(self) -> Sequence[PlaylistSignal]:
        """Return selected playlist references only."""
        return self.signals

    async def replace(self, signals: Sequence[PlaylistSignal]) -> None:
        """Capture changed playlist opt-ins."""
        self.signals = list(signals)

    async def add(self, item: Feedback) -> None:
        """Capture explicit feedback for the current track."""
        self.feedback.append(item)

    async def reset(self) -> None:
        """Clear only local fake personalization state."""
        self.was_reset = True
        self.signals = []
        self.feedback = []

    async def export_data(self) -> dict[str, object]:
        """Return a privacy-bounded fake export payload."""
        return {
            "profile": None,
            "feedback": [item.model_dump(mode="json") for item in self.feedback],
        }

    async def record_action(
        self, entry: ActionHistoryEntry, *, score_json: Sequence[str | None]
    ) -> None:
        """Capture URI-only action sessions after explicit approval."""
        assert len(entry.spotify_uris) == len(score_json)
        self.actions.append(entry)


class FakeProfileService:
    """Return a deterministic compact profile without Spotify endpoint calls."""

    async def refresh(self) -> ProfileSummary:
        """Expose a profile summary that can be rendered by the TUI."""
        return ProfileSummary(
            favorite_artists=["Artist"],
            favorite_track_uris=["spotify:track:4uLU6hMCjMI75M1A2tKUQC"],
            recent_track_uris=[],
            updated_at=datetime.now(UTC),
        )


def resolved_intent() -> ListeningIntent:
    """Create a direct intent that makes the fake preview inspectable."""
    return intent(action=PlaybackAction.PLAY_NOW, artists=["Artist"])


async def test_unconfigured_shell_explains_the_missing_client_id() -> None:
    """The initial TUI tells a new user how to enable Spotify sign-in."""
    app = SpotifyControlApp(
        configuration_error="Copy .env.example to .env and set TUNETRAIL_SPOTIFY_CLIENT_ID."
    )

    async with app.run_test():
        assert "Spotify is not configured" in str(app.query_one("#status", Static).render())
        assert ".env.example" in str(app.query_one("#notice", Static).render())


async def test_queue_preview_can_be_cancelled_without_a_spotify_mutation() -> None:
    """Search selection and cancellation stay entirely local until confirmation."""
    spotify = FakeSpotify()
    app = SpotifyControlApp(spotify=cast(SpotifyClient, spotify))

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await pilot.click("#preview-play")
        await pilot.pause()
        assert "Select a Spotify catalog result" in str(app.query_one("#notice", Static).render())
        command = app.query_one("#command-input", Input)
        command.focus()
        command.value = "search track Track"
        await pilot.press("enter")
        await pilot.pause()
        results = cast(Select[str], app.query_one("#catalog-results", Select))
        results.value = spotify.track.spotify_id
        await pilot.pause()
        await pilot.click("#preview-play")
        await pilot.pause()
        assert "Queue preview" in str(app.query_one("#queue-preview", Static).render())
        await pilot.click("#cancel-preview")

    assert spotify.started == []
    assert spotify.appended == []


async def test_confirmed_preview_starts_playback_on_the_selected_device() -> None:
    """Only an explicit confirmation invokes Spotify playback."""
    spotify = FakeSpotify()
    app = SpotifyControlApp(spotify=cast(SpotifyClient, spotify))

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        command = app.query_one("#command-input", Input)
        command.focus()
        command.value = "search track Track"
        await pilot.press("enter")
        await pilot.pause()
        cast(
            Select[str], app.query_one("#catalog-results", Select)
        ).value = spotify.track.spotify_id
        await pilot.pause()
        await pilot.click("#preview-play")
        await pilot.pause()
        await pilot.click("#confirm-preview")
        await pilot.pause()

    assert spotify.started == [[spotify.track.spotify_uri]]


async def test_confirmed_append_adds_selected_preview_tracks() -> None:
    """Append has the same review-and-confirm safety boundary as play-now."""
    spotify = FakeSpotify()
    app = SpotifyControlApp(spotify=cast(SpotifyClient, spotify))

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        command = app.query_one("#command-input", Input)
        command.focus()
        command.value = "search track Track"
        await pilot.press("enter")
        await pilot.pause()
        cast(
            Select[str], app.query_one("#catalog-results", Select)
        ).value = spotify.track.spotify_id
        await pilot.pause()
        await pilot.click("#preview-append")
        await pilot.pause()
        await pilot.click("#confirm-preview")
        await pilot.pause()

    assert spotify.appended == [spotify.track.spotify_uri]


async def test_command_history_is_available_only_for_the_running_session() -> None:
    """Submitted commands can be recalled with the keyboard but are not persisted."""
    spotify = FakeSpotify()
    app = SpotifyControlApp(spotify=cast(SpotifyClient, spotify))

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        command = app.query_one("#command-input", CommandInput)
        command.focus()
        command.value = "search track Track"
        await pilot.press("enter")
        await pilot.pause()
        command.value = ""
        await pilot.press("up")

        assert command.value == "search track Track"


async def test_natural_request_uses_graph_preview_then_approval_resume() -> None:
    """Non-search input delegates to the graph and cannot invoke local player controls directly."""
    spotify = FakeSpotify()
    request_graph = FakeRequestGraph(spotify.track)
    app = SpotifyControlApp(
        spotify=cast(SpotifyClient, spotify), request_graph=cast(RequestGraph, request_graph)
    )

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        command = app.query_one("#command-input", Input)
        command.focus()
        command.value = "Play Artist"
        await pilot.press("enter")
        await pilot.pause()
        assert "Queue preview" in str(app.query_one("#queue-preview", Static).render())
        assert "artist: Artist" in str(app.query_one("#intent-summary", Static).render())
        await pilot.click("#confirm-preview")
        await pilot.pause()

    assert request_graph.requests == ["Play Artist"]
    assert request_graph.decisions == [ApprovalDecision.APPROVE]
    assert spotify.started == []


async def test_natural_preview_can_regenerate_then_cancel_without_mutation() -> None:
    """Regenerate and cancel are graph decisions that do not call Spotify player APIs."""
    spotify = FakeSpotify()
    request_graph = FakeRequestGraph(spotify.track)
    app = SpotifyControlApp(
        spotify=cast(SpotifyClient, spotify), request_graph=cast(RequestGraph, request_graph)
    )

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        command = app.query_one("#command-input", Input)
        command.focus()
        command.value = "Play Artist"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click("#regenerate-preview")
        await pilot.pause()
        await pilot.click("#cancel-preview")
        await pilot.pause()

    assert request_graph.decisions == [ApprovalDecision.REGENERATE, ApprovalDecision.CANCEL]
    assert spotify.started == []
    assert spotify.appended == []


async def test_profile_controls_use_explicit_opt_in_feedback_reset_and_private_export(
    tmp_path: Path,
) -> None:
    """Profile actions persist local signals only and never invoke Spotify player mutations."""
    spotify = FakeSpotify()
    store = FakePersonalizationStore()
    export_path = tmp_path / "profile-export.json"
    app = SpotifyControlApp(
        spotify=cast(SpotifyClient, spotify),
        personalization_store=cast(PersonalizationStore, store),
        profile_service=cast(ProfileRefresher, FakeProfileService()),
        profile_export_path=export_path,
    )

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        app.query_one("#refresh-profile", Button).press()
        await pilot.pause()
        assert "1 artists" in str(app.query_one("#profile-summary", Static).render())
        app.query_one("#load-profile-playlists", Button).press()
        await pilot.pause()
        playlist_select = cast(Select[str], app.query_one("#profile-playlists", Select))
        playlist_select.value = "playlist-1"
        await pilot.pause()
        app.query_one("#toggle-playlist-signal", Button).press()
        await pilot.pause()
        assert [signal.spotify_playlist_id for signal in store.signals] == ["playlist-1"]
        app.query_one("#feedback-like", Button).press()
        await pilot.pause()
        assert [item.kind for item in store.feedback] == [FeedbackKind.LIKE]
        app.query_one("#export-profile", Button).press()
        await pilot.pause()
        assert export_path.exists()
        assert export_path.stat().st_mode & 0o777 == 0o600
        app.query_one("#reset-profile", Button).press()
        await pilot.pause()

    assert store.was_reset
    assert spotify.started == []
    assert spotify.appended == []


async def test_explicit_preview_records_a_local_session_only_after_confirmation() -> None:
    """Explicit catalog playback writes URI history only after the user confirms the preview."""
    spotify = FakeSpotify()
    store = FakePersonalizationStore()
    app = SpotifyControlApp(
        spotify=cast(SpotifyClient, spotify),
        personalization_store=cast(PersonalizationStore, store),
    )

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        command = app.query_one("#command-input", Input)
        command.value = "search track Track"
        await pilot.press("enter")
        await pilot.pause()
        cast(
            Select[str], app.query_one("#catalog-results", Select)
        ).value = spotify.track.spotify_id
        await pilot.pause()
        await pilot.click("#preview-play")
        await pilot.pause()
        assert store.actions == []
        await pilot.click("#confirm-preview")
        await pilot.pause()

    assert [entry.spotify_uris for entry in store.actions] == [[spotify.track.spotify_uri]]


def test_catalog_commands_require_an_explicit_supported_type() -> None:
    """Phase 2 commands stay deterministic rather than interpreting natural language."""
    assert parse_catalog_search_command("search album Red") == (CatalogItemKind.ALBUM, "Red")
    try:
        parse_catalog_search_command("play something calm")
    except CatalogSearchCommandError as error:
        assert "search" in str(error)
    else:
        raise AssertionError("an implicit natural-language command was accepted")
