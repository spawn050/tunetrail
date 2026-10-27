"""Application entry point and runtime service composition."""

import httpx

from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.graph.request_graph import NaturalLanguageRequestGraph
from tunetrail.lastfm.client import LastfmClient
from tunetrail.llm.factory import LLMConfigurationError, OpenRouterLLMFactory
from tunetrail.musicbrainz.cache import CachedRecordingLookup
from tunetrail.musicbrainz.client import MusicBrainzClient
from tunetrail.musicbrainz.enrichment import MusicBrainzEnrichment
from tunetrail.paths import AppPaths
from tunetrail.persistence.action_history import FileActionHistory
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore
from tunetrail.recommendation.lastfm import LastfmEnrichmentService
from tunetrail.recommendation.profile import SpotifyProfileService
from tunetrail.recommendation.resolver import SpotifyCandidateResolver
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
    lastfm_enrichment = None
    if settings.lastfm_api_key is not None and settings.lastfm_username is not None:
        lastfm_enrichment = LastfmEnrichmentService(
            lastfm=LastfmClient(
                api_key=settings.lastfm_api_key,
                username=settings.lastfm_username,
                http_client=http_client,
                api_url=settings.lastfm_api_url,
            ),
            resolver=SpotifyCandidateResolver(spotify),
            store=personalization_store,
            username=settings.lastfm_username,
        )
    musicbrainz_enrichment = (
        MusicBrainzEnrichment(
            CachedRecordingLookup(MusicBrainzClient(http_client), personalization_store)
        )
        if settings.musicbrainz_enabled
        else None
    )
    request_graph = None
    natural_language_error: str | None = None
    if settings.llm_model is not None or settings.openrouter_api_key is not None:
        try:
            request_graph = NaturalLanguageRequestGraph(
                parser=OpenRouterLLMFactory(settings).create_intent_parser(),
                spotify=spotify,
                store=personalization_store,
                lastfm_enrichment=lastfm_enrichment,
                musicbrainz_enrichment=musicbrainz_enrichment,
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
        personalization_store=personalization_store,
        profile_service=SpotifyProfileService(spotify=spotify, store=personalization_store),
        profile_export_path=paths.profile_export_file,
    )


def main() -> None:
    """Start TuneTrail's terminal user interface."""
    build_app().run()
