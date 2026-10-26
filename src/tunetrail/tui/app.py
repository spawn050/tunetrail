"""Minimal Textual shell for Spotify authorization and player control."""

import webbrowser
from collections.abc import Callable, Sequence

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Select, Static

from tunetrail.auth.service import (
    SpotifyAuthenticator,
    SpotifyAuthorizationError,
    SpotifyNotAuthenticatedError,
)
from tunetrail.domain.models import Device, PlaybackState
from tunetrail.spotify.client import SpotifyClient
from tunetrail.spotify.errors import SpotifyApiError
from tunetrail.spotify.models import SpotifyPayloadError

BrowserOpener = Callable[[str], bool]


class SpotifyControlApp(App[None]):
    """Display Spotify auth, device, now-playing, and basic control state."""

    TITLE = "TuneTrail"
    CSS = """
    #status, #device, #now-playing, #notice { padding: 1; }
    #controls Button { margin-right: 1; }
    Select { margin: 0 1; }
    """

    def __init__(
        self,
        *,
        authenticator: SpotifyAuthenticator | None = None,
        spotify: SpotifyClient | None = None,
        configuration_error: str | None = None,
        open_browser: BrowserOpener = webbrowser.open,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._authenticator = authenticator
        self._spotify = spotify
        self._configuration_error = configuration_error
        self._open_browser = open_browser
        self._http_client = http_client
        self._selected_device_id: str | None = None

    def compose(self) -> ComposeResult:
        """Compose the deliberately small Phase 1 status and control surface."""
        yield Header()
        with Vertical():
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
            yield Static("", id="notice")
        yield Footer()

    def on_mount(self) -> None:
        """Load visible Spotify state once Textual has mounted the widgets."""
        if self._configuration_error is not None:
            self._set_status("Spotify is not configured")
            self._set_notice(self._configuration_error)
            return
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
        if isinstance(event.value, str):
            self._selected_device_id = event.value
            self.change_device()

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
        """Synchronize the three visible status regions from Spotify."""
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

    def _update_devices(self, devices: Sequence[Device]) -> None:
        """Show available devices and maintain a valid selection."""
        selectable = [device for device in devices if device.id is not None]
        options = [(device.name, device.id) for device in selectable if device.id is not None]
        select = self.query_one("#device-select", Select[str])
        selectable_ids = {device.id for device in selectable}
        if self._selected_device_id not in selectable_ids:
            self._selected_device_id = None
        select.set_options(options)
        active = next((device for device in selectable if device.is_active), None)
        if self._selected_device_id is None and active is not None and active.id is not None:
            self._selected_device_id = active.id
            select.value = active.id
        selected = next(
            (device for device in selectable if device.id == self._selected_device_id), active
        )
        self.query_one("#device", Static).update(
            f"Device: {selected.name if selected is not None else 'none available'}"
        )

    def _update_playback(self, playback: PlaybackState | None) -> None:
        """Show the current track without retaining any listening history."""
        if playback is None or playback.track is None:
            self.query_one("#now-playing", Static).update("Now playing: nothing")
            return
        artists = ", ".join(playback.track.artists)
        status = "playing" if playback.is_playing else "paused"
        self.query_one("#now-playing", Static).update(
            f"Now playing: {playback.track.title} — {artists} ({status})"
        )

    def _set_status(self, message: str) -> None:
        """Set the short authorization or connectivity status line."""
        self.query_one("#status", Static).update(message)

    def _set_notice(self, message: str) -> None:
        """Set an actionable user-facing status notice."""
        self.query_one("#notice", Static).update(message)
