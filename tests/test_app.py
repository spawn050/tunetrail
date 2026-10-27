"""Tests for application composition and the Textual shell."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from textual.widgets import Input, Select, Static

from tests.factories import resolved_track
from tunetrail.domain.models import (
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    PlaybackState,
    ResolvedTrack,
)
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


def test_catalog_commands_require_an_explicit_supported_type() -> None:
    """Phase 2 commands stay deterministic rather than interpreting natural language."""
    assert parse_catalog_search_command("search album Red") == (CatalogItemKind.ALBUM, "Red")
    try:
        parse_catalog_search_command("play something calm")
    except CatalogSearchCommandError as error:
        assert "search" in str(error)
    else:
        raise AssertionError("an implicit natural-language command was accepted")
