"""Application entry point and runtime service composition."""

import httpx

from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.paths import AppPaths
from tunetrail.spotify.client import SpotifyClient
from tunetrail.tui.app import SpotifyControlApp


def build_app() -> SpotifyControlApp:
    """Build TuneTrail's Spotify control interface from local settings and paths."""
    paths = AppPaths.default()
    paths.create_directories()
    settings = Settings.load(paths)
    if settings.spotify_client_id is None:
        return SpotifyControlApp(
            configuration_error="Set TUNETRAIL_SPOTIFY_CLIENT_ID before signing in."
        )
    http_client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    authenticator = SpotifyAuthenticator(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        scopes=settings.spotify_scopes,
        credentials=FileCredentialStore(paths.auth_file),
        http_client=http_client,
        accounts_url=settings.spotify_accounts_url,
    )
    spotify = SpotifyClient(authenticator, http_client, api_url=settings.spotify_api_url)
    return SpotifyControlApp(
        authenticator=authenticator,
        spotify=spotify,
        http_client=http_client,
    )


def main() -> None:
    """Start TuneTrail's terminal user interface."""
    build_app().run()
