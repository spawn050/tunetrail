"""Tests for application composition and the Textual shell."""

from textual.widgets import Static

from tunetrail.tui.app import SpotifyControlApp


async def test_unconfigured_shell_explains_the_missing_client_id() -> None:
    """The initial TUI tells a new user how to enable Spotify sign-in."""
    app = SpotifyControlApp(
        configuration_error="Set TUNETRAIL_SPOTIFY_CLIENT_ID before signing in."
    )

    async with app.run_test():
        assert "Spotify is not configured" in str(app.query_one("#status", Static).render())
        assert "TUNETRAIL_SPOTIFY_CLIENT_ID" in str(app.query_one("#notice", Static).render())
