"""Application entry point and runtime service composition."""

import httpx

from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.graph.request_graph import NaturalLanguageRequestGraph
from tunetrail.llm.factory import LLMConfigurationError, OpenRouterLLMFactory
from tunetrail.paths import AppPaths
from tunetrail.persistence.action_history import FileActionHistory
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore
from tunetrail.spotify.client import SpotifyClient
from tunetrail.tui.app import SpotifyControlApp


def build_app() -> SpotifyControlApp:
    """Build TuneTrail's Spotify control interface from local settings and paths."""
    paths = AppPaths.default()
    paths.create_directories()
    settings = Settings.load()
    if settings.spotify_client_id is None:
        return SpotifyControlApp(
            configuration_error="Copy .env.example to .env and set TUNETRAIL_SPOTIFY_CLIENT_ID."
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
    personalization_store = SQLitePersonalizationStore(paths.database_file)
    request_graph = None
    natural_language_error: str | None = None
    if settings.llm_model is not None or settings.openrouter_api_key is not None:
        try:
            request_graph = NaturalLanguageRequestGraph(
                parser=OpenRouterLLMFactory(settings).create_intent_parser(),
                spotify=spotify,
                store=personalization_store,
            )
        except LLMConfigurationError as error:
            natural_language_error = str(error)
    return SpotifyControlApp(
        authenticator=authenticator,
        spotify=spotify,
        http_client=http_client,
        action_history=FileActionHistory(paths.action_history_file),
        request_graph=request_graph,
        natural_language_error=natural_language_error,
    )


def main() -> None:
    """Start TuneTrail's terminal user interface."""
    build_app().run()
