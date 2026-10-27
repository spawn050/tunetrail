"""Minimal Textual shell for Spotify authorization and player control."""

import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast

import httpx
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Select, Static

from tunetrail.auth.service import (
    SpotifyAuthenticator,
    SpotifyAuthorizationError,
    SpotifyNotAuthenticatedError,
)
from tunetrail.domain.models import (
    ActionHistoryEntry,
    ActionOutcome,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    PlaybackAction,
    PlaybackState,
    QueuePreview,
)
from tunetrail.persistence.action_history import ActionHistoryError, FileActionHistory
from tunetrail.services.queue_preview import build_queue_preview
from tunetrail.spotify.client import SpotifyClient
from tunetrail.spotify.errors import SpotifyApiError
from tunetrail.spotify.models import SpotifyPayloadError

BrowserOpener = Callable[[str], bool]


class CatalogSearchCommandError(ValueError):
    """Raised when a command is not an explicit supported catalog search."""


class CommandInput(Input):
    """Command field with transient, keyboard-accessible in-session history."""

    def on_mount(self) -> None:
        """Initialize a history that disappears when the application exits."""
        self._history: list[str] = []
        self._history_index: int | None = None

    def remember(self, command: str) -> None:
        """Store a non-empty command once for this running TUI session only."""
        normalized = command.strip()
        if normalized and (not self._history or self._history[-1] != normalized):
            self._history.append(normalized)
        self._history_index = None

    def on_key(self, event: events.Key) -> None:
        """Cycle through submitted commands with up/down without persisting them."""
        if event.key not in {"up", "down"} or not self._history:
            return
        event.prevent_default()
        event.stop()
        if event.key == "up":
            index = (
                len(self._history) - 1 if self._history_index is None else self._history_index - 1
            )
            self._history_index = max(index, 0)
            self.value = self._history[self._history_index]
        elif self._history_index is not None:
            index = self._history_index + 1
            if index >= len(self._history):
                self._history_index = None
                self.value = ""
            else:
                self._history_index = index
                self.value = self._history[index]
        self.cursor_position = len(self.value)


def parse_catalog_search_command(command: str) -> tuple[CatalogItemKind, str]:
    """Parse `search <type> <query>` without interpreting natural language."""
    parts = command.strip().split(maxsplit=2)
    if len(parts) != 3 or parts[0].lower() != "search":
        raise CatalogSearchCommandError("Use: search track|artist|album|playlist <query>")
    try:
        kind = CatalogItemKind(parts[1].lower())
    except ValueError as error:
        raise CatalogSearchCommandError(
            "Search type must be track, artist, album, or playlist."
        ) from error
    if not parts[2].strip():
        raise CatalogSearchCommandError("Search query cannot be empty.")
    return kind, parts[2].strip()


class SpotifyControlApp(App[None]):
    """Display Spotify control plus explicit, reviewable catalog queue actions."""

    TITLE = "TuneTrail"
    BINDINGS = [
        ("ctrl+k", "focus_command", "Command"),
        ("escape", "cancel_preview", "Cancel preview"),
        ("ctrl+r", "refresh", "Refresh"),
    ]

    CSS = """
    #status, #device, #now-playing, #notice, #selection { padding: 1; }
    #queue-preview, #current-queue, #action-history, #shortcuts { padding: 1; }
    #controls Button { margin-right: 1; }
    #catalog-actions Button, #preview-actions Button { margin-right: 1; }
    Select { margin: 0 1; }
    Input { margin: 0 1; }
    """

    def __init__(
        self,
        *,
        authenticator: SpotifyAuthenticator | None = None,
        spotify: SpotifyClient | None = None,
        configuration_error: str | None = None,
        open_browser: BrowserOpener = webbrowser.open,
        http_client: httpx.AsyncClient | None = None,
        action_history: FileActionHistory | None = None,
    ) -> None:
        super().__init__()
        self._authenticator = authenticator
        self._spotify = spotify
        self._configuration_error = configuration_error
        self._open_browser = open_browser
        self._http_client = http_client
        self._action_history = action_history
        self._selected_device_id: str | None = None
        self._updating_devices = False
        self._catalog_results: dict[str, CatalogItem] = {}
        self._selected_catalog_item: CatalogItem | None = None
        self._queue_preview: QueuePreview | None = None
        self._playback: PlaybackState | None = None

    def compose(self) -> ComposeResult:
        """Compose the keyboard-first Phase 2 queue workflow."""
        yield Header()
        with VerticalScroll(id="content"):
            yield Static("Checking Spotify authorization…", id="status")
            yield Select[str]([], prompt="Select playback device", id="device-select")
            yield Static("Device: not connected", id="device")
            yield Static("Now playing: unavailable", id="now-playing")
            with Horizontal(id="controls"):
                yield Button("Sign in", id="sign-in", variant="primary")
                yield Button("Refresh", id="refresh")
                yield Button("Previous", id="previous")
                yield Button("Play", id="play")
                yield Button("Pause", id="pause")
                yield Button("Next", id="next")
                yield Button("Log out", id="logout", variant="error")
            yield CommandInput(
                placeholder="search track|artist|album|playlist <query>", id="command-input"
            )
            yield Static(
                "Type a catalog command; ↑/↓ history; Ctrl-K command; Esc cancel; Ctrl-R refresh.",
                id="shortcuts",
            )
            yield Static(
                "Spotify catalog results and playback data are provided by Spotify.",
                id="attribution",
            )
            yield Select[str]([], prompt="Select a catalog result", id="catalog-results")
            yield Static("No catalog result selected", id="selection")
            with Horizontal(id="catalog-actions"):
                yield Button("Preview play now", id="preview-play")
                yield Button("Preview append", id="preview-append")
            yield Static("No pending queue preview", id="queue-preview")
            with Horizontal(id="preview-actions"):
                yield Button("Confirm", id="confirm-preview", variant="primary")
                yield Button("Cancel", id="cancel-preview")
            yield Static("Current queue: unavailable", id="current-queue")
            yield Static("Action history: none", id="action-history")
            yield Static("", id="notice")
        yield Footer()

    def on_mount(self) -> None:
        """Load visible Spotify state once Textual has mounted the widgets."""
        if self._configuration_error is not None:
            self._set_status("Spotify is not configured")
            self._set_notice(self._configuration_error)
            return
        self.set_interval(1, self._refresh_playback_progress)
        self.query_one("#command-input", CommandInput).focus()
        self._update_action_history()
        self.refresh_status()

    async def on_unmount(self) -> None:
        """Close the runtime-owned HTTP client when the TUI exits."""
        if self._http_client is not None:
            await self._http_client.aclose()

    @on(Button.Pressed, "#sign-in")
    def begin_sign_in(self) -> None:
        """Open Spotify's authorization page and wait for the local callback."""
        self.sign_in()

    @on(Button.Pressed, "#refresh")
    def refresh_button(self) -> None:
        """Fetch current auth, device, and playback state."""
        self.refresh_status()

    @on(Button.Pressed, "#logout")
    def logout_button(self) -> None:
        """Forget locally stored Spotify token material."""
        self.logout()

    @on(Button.Pressed, "#previous")
    def previous_button(self) -> None:
        """Request the previous track on the selected device."""
        self.control("previous_track")

    @on(Button.Pressed, "#play")
    def play_button(self) -> None:
        """Resume playback on the selected device."""
        self.control("resume")

    @on(Button.Pressed, "#pause")
    def pause_button(self) -> None:
        """Pause playback on the selected device."""
        self.control("pause")

    @on(Button.Pressed, "#next")
    def next_button(self) -> None:
        """Request the next track on the selected device."""
        self.control("next_track")

    @on(Select.Changed, "#device-select")
    def select_device(self, event: Select.Changed) -> None:
        """Remember and transfer control to an explicitly chosen device."""
        if self._updating_devices or event.value == self._selected_device_id:
            return
        if isinstance(event.value, str):
            self._selected_device_id = event.value
            self.change_device()

    @on(Input.Submitted, "#command-input")
    def submit_catalog_command(self, event: Input.Submitted) -> None:
        """Search a declared Spotify catalog resource from the command field."""
        command_input = self.query_one("#command-input", CommandInput)
        command_input.remember(event.value)
        try:
            kind, query = parse_catalog_search_command(event.value)
        except CatalogSearchCommandError as error:
            self._set_notice(str(error))
            return
        self.search_catalog(kind, query)

    @on(Select.Changed, "#catalog-results")
    def select_catalog_result(self, event: Select.Changed) -> None:
        """Keep selection separate from preview construction and player mutation."""
        if not isinstance(event.value, str):
            return
        self._selected_catalog_item = self._catalog_results.get(event.value)
        if self._selected_catalog_item is not None:
            self.query_one("#selection", Static).update(
                f"Selected: {self._selected_catalog_item.title}"
            )

    @on(Button.Pressed, "#preview-play")
    def preview_play_button(self) -> None:
        """Prepare a reviewable play-now preview for the selected catalog result."""
        self.preview_selection(PlaybackAction.PLAY_NOW)

    @on(Button.Pressed, "#preview-append")
    def preview_append_button(self) -> None:
        """Prepare a reviewable append preview for the selected catalog result."""
        self.preview_selection(PlaybackAction.APPEND_QUEUE)

    @on(Button.Pressed, "#confirm-preview")
    def confirm_preview_button(self) -> None:
        """Execute only a previously previewed, explicitly confirmed action."""
        self.confirm_preview()

    @on(Button.Pressed, "#cancel-preview")
    def cancel_preview_button(self) -> None:
        """Discard a preview without contacting Spotify's player endpoints."""
        self.action_cancel_preview()

    def action_focus_command(self) -> None:
        """Focus the explicit catalog command input."""
        self.query_one("#command-input", CommandInput).focus()

    def action_cancel_preview(self) -> None:
        """Discard the local preview; cancellation is always non-mutating."""
        if self._queue_preview is not None:
            self._queue_preview = None
            self._update_queue_preview()
            self._set_notice("Queue preview cancelled; Spotify was not changed.")

    def action_refresh(self) -> None:
        """Refresh authorization, playback, device, and visible queue state."""
        self.refresh_status()

    @work(exclusive=True)
    async def search_catalog(self, kind: CatalogItemKind, query: str) -> None:
        """Load explicit catalog results without preparing or changing a queue."""
        if self._spotify is None:
            self._set_notice(self._configuration_error or "Spotify is not configured")
            return
        try:
            results = await self._spotify.search_catalog(query, kind=kind)
        except (
            SpotifyApiError,
            SpotifyAuthorizationError,
            SpotifyPayloadError,
            SpotifyNotAuthenticatedError,
        ) as error:
            self._set_notice(str(error))
            return
        self._catalog_results = {result.spotify_id: result for result in results}
        self._selected_catalog_item = None
        select = cast(Select[str], self.query_one("#catalog-results", Select))
        select.set_options(
            [
                (
                    f"{result.title}{f' — {result.subtitle}' if result.subtitle else ''}",
                    result.spotify_id,
                )
                for result in results
            ]
        )
        self.query_one("#selection", Static).update(
            "Select a catalog result" if results else "No matching Spotify catalog results"
        )
        self._set_notice(
            f"Found {len(results)} Spotify {kind.value} result{'s' if len(results) != 1 else ''}."
        )

    @work(exclusive=True)
    async def preview_selection(self, action: PlaybackAction) -> None:
        """Resolve a selection into a local, reviewable preview with no player mutation."""
        if self._spotify is None:
            self._set_notice(self._configuration_error or "Spotify is not configured")
            return
        item = self._selected_catalog_item
        if item is None:
            self._set_notice("Select a Spotify catalog result before creating a preview.")
            return
        try:
            tracks = await self._spotify.tracks_for_catalog_item(item)
            self._queue_preview = build_queue_preview(
                tracks,
                action=action,
                query=item.title,
                explanation=f"Selected from Spotify {item.kind.value} search.",
            )
        except (
            SpotifyApiError,
            SpotifyAuthorizationError,
            SpotifyPayloadError,
            SpotifyNotAuthenticatedError,
            ValueError,
        ) as error:
            self._set_notice(str(error))
            return
        self._update_queue_preview()
        self._set_notice("Review the queue preview, then confirm or cancel.")

    @work(exclusive=True)
    async def confirm_preview(self) -> None:
        """Apply the local preview only after the user explicitly confirms it."""
        if self._spotify is None:
            self._set_notice(self._configuration_error or "Spotify is not configured")
            return
        preview = self._queue_preview
        if preview is None:
            self._set_notice("There is no queue preview to confirm.")
            return
        uris = [item.track.spotify_uri for item in preview.items]
        try:
            if preview.intent.action is PlaybackAction.PLAY_NOW:
                await self._spotify.start_playback(uris, device_id=self._selected_device_id)
            else:
                for uri in uris:
                    await self._spotify.add_to_queue(uri, device_id=self._selected_device_id)
        except (
            SpotifyApiError,
            SpotifyAuthorizationError,
            SpotifyPayloadError,
            SpotifyNotAuthenticatedError,
        ) as error:
            self._record_action(preview, ActionOutcome.FAILED)
            self._set_notice(f"Spotify action failed; it may have partially completed: {error}")
            return
        self._record_action(preview, ActionOutcome.SUCCEEDED)
        self._queue_preview = None
        self._update_queue_preview()
        self._set_notice("Spotify action completed.")
        await self._refresh_status()

    @work(exclusive=True)
    async def sign_in(self) -> None:
        """Run the browser PKCE flow without blocking Textual's event loop."""
        if self._authenticator is None:
            self._set_notice(self._configuration_error or "Spotify is not configured")
            return
        session, url = self._authenticator.begin_authorization()
        self._set_status("Waiting for Spotify authorization in your browser…")
        self._open_browser(url)
        try:
            await self._authenticator.receive_and_exchange(session)
        except Exception as error:  # OAuth errors are intentionally shown without tracebacks.
            self._set_status("Spotify authorization failed")
            self._set_notice(str(error))
            return
        self._set_notice("Spotify authorization succeeded")
        await self._refresh_status()

    @work(exclusive=True)
    async def logout(self) -> None:
        """Clear credentials and restore unauthenticated shell state."""
        if self._authenticator is None:
            return
        await self._authenticator.logout()
        self._set_status("Not signed in to Spotify")
        self._set_notice("Local Spotify token material was removed")

    @work(exclusive=True)
    async def refresh_status(self) -> None:
        """Fetch all status data while keeping the interface responsive."""
        await self._refresh_status()

    @work(exclusive=True)
    async def change_device(self) -> None:
        """Transfer control to the selected Spotify Connect device."""
        if self._spotify is None or self._selected_device_id is None:
            return
        try:
            await self._spotify.select_device(self._selected_device_id)
        except (
            SpotifyApiError,
            SpotifyAuthorizationError,
            SpotifyPayloadError,
            SpotifyNotAuthenticatedError,
        ) as error:
            self._set_notice(str(error))
            return
        self._set_notice("Selected Spotify device")
        await self._refresh_status()

    @work(exclusive=True)
    async def control(self, method_name: str) -> None:
        """Run one basic player control against the selected device."""
        if self._spotify is None:
            self._set_notice(self._configuration_error or "Spotify is not configured")
            return
        method = getattr(self._spotify, method_name)
        try:
            await method(device_id=self._selected_device_id)
        except (
            SpotifyApiError,
            SpotifyAuthorizationError,
            SpotifyPayloadError,
            SpotifyNotAuthenticatedError,
        ) as error:
            self._set_notice(str(error))
            return
        await self._refresh_status()

    async def _refresh_status(self) -> None:
        """Synchronize visible status and queue regions from Spotify."""
        if self._spotify is None:
            self._set_status("Spotify is not configured")
            return
        try:
            user = await self._spotify.current_user()
            devices = await self._spotify.devices()
            playback = await self._spotify.current_playback()
        except SpotifyNotAuthenticatedError:
            self._set_status("Not signed in to Spotify")
            self._set_notice("Use Sign in to authorize TuneTrail")
            return
        except (SpotifyApiError, SpotifyAuthorizationError, SpotifyPayloadError) as error:
            self._set_status("Spotify is unavailable")
            self._set_notice(str(error))
            return
        self._set_status(f"Signed in as {user.display_name or user.id}")
        self._update_devices(devices)
        self._update_playback(playback)
        try:
            queue = await self._spotify.current_queue()
        except (SpotifyApiError, SpotifyAuthorizationError, SpotifyPayloadError) as error:
            self._update_current_queue(None, f"Current queue unavailable: {error}")
        else:
            self._update_current_queue(queue)

    def _update_devices(self, devices: Sequence[Device]) -> None:
        """Show available devices and maintain a valid selection."""
        selectable = [device for device in devices if device.id is not None]
        options = [(device.name, device.id) for device in selectable if device.id is not None]
        select = cast(Select[str], self.query_one("#device-select", Select))
        selectable_ids = {device.id for device in selectable}
        if self._selected_device_id not in selectable_ids:
            self._selected_device_id = None
        active = next((device for device in selectable if device.is_active), None)
        self._updating_devices = True
        try:
            select.set_options(options)
            if self._selected_device_id is None and active is not None and active.id is not None:
                self._selected_device_id = active.id
                select.value = active.id
        finally:
            self._updating_devices = False
        selected = next(
            (device for device in selectable if device.id == self._selected_device_id), active
        )
        self.query_one("#device", Static).update(
            f"Device: {selected.name if selected is not None else 'none available'}"
        )

    def _update_playback(self, playback: PlaybackState | None) -> None:
        """Show the current track and its transient playback progress."""
        self._playback = playback
        self._refresh_playback_progress()

    def _refresh_playback_progress(self) -> None:
        """Advance only the rendered progress between Spotify state refreshes."""
        playback = self._playback
        if playback is None or playback.track is None:
            self.query_one("#now-playing", Static).update("Now playing: nothing")
            return
        artists = ", ".join(playback.track.artists)
        status = "playing" if playback.is_playing else "paused"
        progress = playback.progress_ms or 0
        if playback.is_playing:
            elapsed = datetime.now(UTC) - playback.fetched_at
            progress = min(
                progress + int(elapsed.total_seconds() * 1_000), playback.track.duration_ms
            )
        self.query_one("#now-playing", Static).update(
            f"Now playing: {playback.track.title} — {artists} ({status}, "
            f"{_format_duration(progress)} / {_format_duration(playback.track.duration_ms)})"
        )

    def _update_current_queue(self, queue: CurrentQueue | None, notice: str | None = None) -> None:
        """Render currently visible queue tracks without persisting their metadata."""
        widget = self.query_one("#current-queue", Static)
        if notice is not None:
            widget.update(notice)
            return
        if queue is None:
            widget.update("Current queue: nothing available")
            return
        upcoming = queue.upcoming[:10]
        if not upcoming:
            widget.update("Current queue: no upcoming tracks")
            return
        lines = ["Current queue:"]
        lines.extend(
            f"{index}. {track.title} — {', '.join(track.artists)}"
            for index, track in enumerate(upcoming, 1)
        )
        widget.update("\n".join(lines))

    def _update_queue_preview(self) -> None:
        """Render the local preview that still requires user confirmation."""
        widget = self.query_one("#queue-preview", Static)
        preview = self._queue_preview
        if preview is None:
            widget.update("No pending queue preview")
            return
        action = (
            "Play now" if preview.intent.action is PlaybackAction.PLAY_NOW else "Append to queue"
        )
        lines = [f"Queue preview — {action}:"]
        lines.extend(
            f"{index}. {item.track.title} — {', '.join(item.track.artists)}"
            for index, item in enumerate(preview.items, 1)
        )
        widget.update("\n".join(lines))

    def _record_action(self, preview: QueuePreview, outcome: ActionOutcome) -> None:
        """Record confirmation outcomes without retaining queries or catalog copies."""
        if self._action_history is None:
            return
        try:
            self._action_history.record(
                ActionHistoryEntry(
                    action=preview.intent.action,
                    spotify_uris=[item.track.spotify_uri for item in preview.items],
                    device_id=self._selected_device_id,
                    outcome=outcome,
                    created_at=datetime.now(UTC),
                )
            )
        except ActionHistoryError as error:
            self._set_notice(str(error))
        self._update_action_history()

    def _update_action_history(self) -> None:
        """Render concise local action records, never search strings or track metadata."""
        widget = self.query_one("#action-history", Static)
        if self._action_history is None:
            widget.update("Action history: unavailable")
            return
        try:
            entries = self._action_history.recent(limit=5)
        except ActionHistoryError as error:
            widget.update(str(error))
            return
        if not entries:
            widget.update("Action history: none")
            return
        lines = ["Action history:"]
        lines.extend(
            f"{entry.created_at.strftime('%H:%M')} — {entry.action.value} "
            f"({len(entry.spotify_uris)} track{'s' if len(entry.spotify_uris) != 1 else ''}) "
            f"{entry.outcome.value}"
            for entry in entries
        )
        widget.update("\n".join(lines))

    def _set_status(self, message: str) -> None:
        """Set the short authorization or connectivity status line."""
        self.query_one("#status", Static).update(message)

    def _set_notice(self, message: str) -> None:
        """Set an actionable user-facing status notice."""
        self.query_one("#notice", Static).update(message)


def _format_duration(milliseconds: int) -> str:
    """Format a non-negative millisecond value for compact TUI progress text."""
    total_seconds = max(milliseconds, 0) // 1_000
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"
