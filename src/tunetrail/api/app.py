"""Local API; all provider state and playback proposals stay on the server."""

import secrets
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from tunetrail.api.schemas import (
    ApprovalInput,
    ChatInput,
    DiscoveryInput,
    FeedbackInput,
    PreferenceInput,
    PreviewInput,
    ProposalInput,
    ResetInput,
)
from tunetrail.api.security import LocalSecurityMiddleware
from tunetrail.auth.service import SpotifyAuthenticator, SpotifyAuthorizationError
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.domain.models import ExplicitPreference, PlaybackAction, PreferenceSource
from tunetrail.llm.factory import OpenRouterLLMFactory
from tunetrail.musicbrainz.client import MusicBrainzClient
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore
from tunetrail.reccobeats.client import ReccoBeatsEnricher
from tunetrail.recommendation.explicit_profile import ExplicitProfileService, PreferenceChatParser
from tunetrail.recommendation.independent_discovery import (
    EnrichedCandidate,
    IndependentDiscovery,
    IndependentSeed,
)
from tunetrail.recommendation.ranking import rank_candidates
from tunetrail.recommendation.spotify_preview import build_preview
from tunetrail.spotify.approval import ApprovalError
from tunetrail.spotify.client import SpotifyClient
from tunetrail.spotify.errors import SpotifyApiError


@dataclass(frozen=True, slots=True)
class Proposal:
    action: PlaybackAction
    uris: tuple[str, ...]
    device_id: str | None
    expires: float


class Services:
    """Runtime dependencies and bounded, ephemeral discovery/preview state."""

    def __init__(self, settings: Settings, database: Path, credentials: Path) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self.profile = ExplicitProfileService(SQLitePersonalizationStore(database))
        self.auth = SpotifyAuthenticator(
            client_id=settings.spotify_client_id or "",
            redirect_uri=settings.spotify_redirect_uri,
            scopes=settings.spotify_scopes,
            credentials=FileCredentialStore(credentials),
            http_client=self.http,
            accounts_url=settings.spotify_accounts_url,
        )
        self.spotify = SpotifyClient(self.auth, self.http, api_url=settings.spotify_api_url)
        self.discovery = IndependentDiscovery(MusicBrainzClient(self.http))
        self.enricher = ReccoBeatsEnricher(self.http)
        self.parser: PreferenceChatParser | None = None
        self.discoveries: dict[str, tuple[float, tuple[EnrichedCandidate, ...]]] = {}
        self.proposals: dict[str, Proposal] = {}

    def prune(self) -> None:
        now = time.monotonic()
        self.discoveries = {key: value for key, value in self.discoveries.items() if value[0] > now}
        self.proposals = {
            key: value for key, value in self.proposals.items() if value.expires > now
        }


def create_app(services: Services, *, port: int = 8766) -> FastAPI:
    """Build an app for one loopback origin; callers own the service lifetime."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    app.add_middleware(LocalSecurityMiddleware, port=port, token=token)

    @app.exception_handler(SpotifyApiError)
    @app.exception_handler(SpotifyAuthorizationError)
    async def spotify_error(_request: object, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Spotify unavailable or not authenticated"}, status_code=502)

    @app.exception_handler(ValueError)
    async def input_error(_request: object, _error: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid input or conflicting local state"}, status_code=400)

    @app.get("/api/session")
    async def session() -> dict[str, str]:
        return {
            "token": token,
            "chat_disclosure": "Chat is sent to the configured hosted LLM and leaves this machine.",
        }

    @app.post("/api/chat")
    async def chat(body: ChatInput) -> object:
        parser = (
            services.parser or OpenRouterLLMFactory(services.settings).create_preference_parser()
        )
        try:
            return await services.profile.interpret_chat(body.message, parser)
        except ValueError:
            raise
        except Exception as error:
            raise HTTPException(502, "Chat provider unavailable") from error

    @app.get("/api/preferences")
    async def preferences() -> object:
        return await services.profile.list_preferences()

    def preference(body: PreferenceInput) -> ExplicitPreference:
        return ExplicitPreference(
            subject=body.subject,
            name=body.name,
            artist=body.artist,
            musicbrainz_id=str(body.musicbrainz_id) if body.musicbrainz_id else None,
            strength=body.strength,
            source=PreferenceSource.DIRECT,
            updated_at=datetime.now(UTC),
        )

    @app.post("/api/preferences")
    async def set_preference(body: PreferenceInput) -> dict[str, bool]:
        await services.profile.set_preference(preference(body))
        return {"saved": True}

    @app.post("/api/preferences/remove")
    async def remove_preference(body: PreferenceInput) -> dict[str, bool]:
        await services.profile.remove(preference(body))
        return {"removed": True}

    @app.post("/api/preferences/reset")
    async def reset_preferences(body: ResetInput) -> dict[str, bool]:
        await services.profile.reset()
        return {"reset": True}

    @app.post("/api/feedback")
    async def feedback(body: FeedbackInput) -> dict[str, bool]:
        await services.profile.record_feedback(
            subject=body.subject,
            name=body.name,
            artist=body.artist,
            musicbrainz_id=str(body.musicbrainz_id) if body.musicbrainz_id else None,
            response=body.response,
            source=PreferenceSource(body.source),
        )
        return {"processed": True}

    @app.get("/api/search")
    async def search(q: str = Query(min_length=1, max_length=200)) -> object:
        return await services.spotify.search_tracks(q, limit=10)

    @app.get("/api/devices")
    async def devices() -> object:
        return await services.spotify.devices()

    @app.post("/api/discovery")
    async def discover(body: DiscoveryInput) -> object:
        services.prune()
        if len(services.discoveries) >= 100:
            raise HTTPException(429, "Too many active discoveries")
        result = await services.discovery.discover(
            [IndependentSeed(seed.kind, seed.musicbrainz_id) for seed in body.seeds],
            limit=body.limit,
            enricher=services.enricher if body.enrich else None,
        )
        items = result.enriched or tuple(
            EnrichedCandidate(c, None, "not requested") for c in result.candidates
        )
        identifier = secrets.token_urlsafe(32)
        services.discoveries[identifier] = (time.monotonic() + 300, items)
        return {"discovery_id": identifier, "result": asdict(result)}

    @app.post("/api/preview")
    async def preview(body: PreviewInput) -> object:
        services.prune()
        discovery = services.discoveries.get(body.discovery_id)
        if discovery is None:
            raise HTTPException(404, "Discovery missing or expired")
        ranked = rank_candidates(
            discovery[1], await services.profile.list_preferences(), body.intent
        )
        result = await build_preview(
            ranked,
            services.spotify,
            services.spotify.approvals,
            action=body.intent.action,
            device_id=body.device_id,
        )
        if result.proposal_id:
            services.proposals[result.proposal_id] = Proposal(
                body.intent.action,
                tuple(match.uri for match in result.matches),
                body.device_id,
                time.monotonic() + 300,
            )
        return asdict(result)

    @app.post("/api/approval")
    async def approve(body: ApprovalInput) -> dict[str, bool]:
        services.prune()
        proposal = services.proposals.pop(body.proposal_id, None)
        if proposal is None:
            raise HTTPException(404, "Proposal missing, expired or already used")
        try:
            services.spotify.approvals.approve(body.proposal_id)
            if proposal.action is PlaybackAction.PLAY_NOW:
                await services.spotify.start_playback(
                    proposal.uris, device_id=proposal.device_id, approval=body.proposal_id
                )
            else:
                await services.spotify.add_to_queue(
                    proposal.uris[0], device_id=proposal.device_id, approval=body.proposal_id
                )
        except ApprovalError as error:
            raise HTTPException(409, "Proposal expired or invalid") from error
        return {"requested": True}

    @app.post("/api/cancel")
    async def cancel(body: ProposalInput) -> dict[str, bool]:
        services.proposals.pop(body.proposal_id, None)
        services.spotify.approvals.cancel(body.proposal_id)
        return {"cancelled": True}

    return app
