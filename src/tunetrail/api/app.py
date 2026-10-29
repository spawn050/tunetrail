"""Local API; all provider state and playback proposals stay on the server."""

import asyncio
import hashlib
import secrets
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tunetrail.api.schemas import (
    ApprovalInput,
    ChatInput,
    DiscoveryInput,
    FeedbackInput,
    PreferenceInput,
    PreviewInput,
    ProposalInput,
    ResetInput,
    SpotifyConnectInput,
)
from tunetrail.api.security import LocalSecurityMiddleware
from tunetrail.auth.loopback import LoopbackCallbackReceiver
from tunetrail.auth.service import SpotifyAuthenticator, SpotifyAuthorizationError
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.domain.models import (
    CatalogItemKind,
    ExplicitPreference,
    PlaybackAction,
    PreferenceSource,
)
from tunetrail.llm.factory import LLMConfigurationError, OpenRouterLLMFactory
from tunetrail.llm.intent_parser import IntentParseError
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
from tunetrail.spotify.approval import ApprovalError, PlayerAction
from tunetrail.spotify.client import SpotifyClient
from tunetrail.spotify.errors import (
    SpotifyApiError,
    SpotifyAuthenticationError,
    SpotifyNoActiveDeviceError,
    SpotifyPermissionError,
    SpotifyRateLimitError,
    SpotifyUnavailableError,
)


@dataclass(frozen=True, slots=True)
class Proposal:
    action: PlaybackAction
    uris: tuple[str, ...]
    device_id: str | None
    expires: float


# Only complete, explicit commands authorize a player mutation. Never infer one
# from model output or a message containing additional instructions.
PLAYER_COMMANDS = {
    "pause": ("pause", "PUT", "/me/player/pause"),
    "pause playback": ("pause", "PUT", "/me/player/pause"),
    "resume": ("resume", "PUT", "/me/player/play"),
    "resume playback": ("resume", "PUT", "/me/player/play"),
    "play": ("resume", "PUT", "/me/player/play"),
    "next": ("next", "POST", "/me/player/next"),
    "next track": ("next", "POST", "/me/player/next"),
    "skip": ("next", "POST", "/me/player/next"),
    "skip track": ("next", "POST", "/me/player/next"),
    "previous": ("previous", "POST", "/me/player/previous"),
    "previous track": ("previous", "POST", "/me/player/previous"),
}

SPOTIFY_CONNECT_COMMANDS = {"connect spotify", "log in to spotify", "login to spotify"}
SPOTIFY_CONNECT_STATUS_COMMANDS = {"spotify connection status", "spotify login status"}
SPOTIFY_CONNECT_CANCEL_COMMANDS = {"cancel spotify connection", "cancel spotify login"}
SPOTIFY_REPLACE_CONFIRMATION = "confirm replace spotify credentials"


class Services:
    """Runtime dependencies and bounded, ephemeral discovery/preview state."""

    def __init__(self, settings: Settings, database: Path, credentials: Path) -> None:
        self.settings = settings
        self.credentials = credentials
        self.connect_lock = asyncio.Lock()
        self.chat_lock = asyncio.Lock()
        self.connect_task: asyncio.Task[None] | None = None
        self.connect_status = "idle"
        self.connect_confirmation: tuple[bytes, float] | None = None
        self.device_lock = asyncio.Lock()
        self.device_confirmation: tuple[str, str, float] | None = None
        self.profile = ExplicitProfileService(SQLitePersonalizationStore(database))
        self.http = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
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
        self.discovery_lock = asyncio.Lock()
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
    @app.exception_handler(httpx.HTTPError)
    async def spotify_error(_request: object, error: Exception) -> JSONResponse:
        if isinstance(error, (SpotifyAuthorizationError, SpotifyAuthenticationError)):
            detail = (
                "Spotify authentication failed. Check credentials and setup; "
                "existing credentials were preserved."
            )
        elif isinstance(error, SpotifyNoActiveDeviceError):
            detail = (
                "No active Spotify device. Open Spotify on a device and refresh the device list."
            )
        elif isinstance(error, SpotifyPermissionError):
            detail = (
                "Spotify denied access. Check Premium, granted scopes, and device restrictions."
            )
        elif isinstance(error, SpotifyRateLimitError):
            detail = "Spotify rate limit reached. Wait before trying again."
        elif isinstance(error, SpotifyUnavailableError):
            detail = "Spotify resource unavailable. Check availability and try another selection."
        else:
            detail = "Provider or network unavailable. Check your connection and try again."
        return JSONResponse({"detail": detail}, status_code=502)

    @app.exception_handler(ValueError)
    async def input_error(_request: object, _error: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid input or conflicting local state"}, status_code=400)

    @app.get("/api/session")
    async def session() -> dict[str, str]:
        return {
            "token": token,
            "chat_disclosure": "Chat is sent to the configured hosted LLM and leaves this machine.",
        }

    @app.get("/api/sources")
    async def sources() -> dict[str, str]:
        # Configuration only: do not make provider calls or expose credential state.
        return {
            "musicbrainz": "Used for independent discovery; availability not checked.",
            "reccobeats": "Optional per discovery; off by default. Availability not checked.",
            "spotify": "Used for catalog and playback; availability not checked.",
            "chat": (
                "OpenRouter key set; availability not checked."
                if services.settings.llm_provider == "openrouter"
                and services.settings.openrouter_api_key
                else "Check hosted LLM configuration; availability not checked."
            ),
        }

    def credential_digest() -> bytes | None:
        try:
            return hashlib.sha256(services.credentials.read_bytes()).digest()
        except FileNotFoundError:
            return None

    @app.get("/api/spotify/connect")
    async def spotify_connect_status() -> dict[str, object]:
        return {
            "configured": bool(services.settings.spotify_client_id),
            "credential_exists": services.credentials.exists(),
            "status": services.connect_status,
        }

    async def start_spotify_connect(
        body: SpotifyConnectInput, *, expected_digest: bytes | None = None
    ) -> dict[str, str]:
        async with services.connect_lock:
            if not services.settings.spotify_client_id:
                raise HTTPException(
                    400, "Set TUNETRAIL_SPOTIFY_CLIENT_ID in .env and restart TuneTrail."
                )
            if services.connect_task and not services.connect_task.done():
                raise HTTPException(409, "Spotify connection is already in progress.")
            original = credential_digest()
            if expected_digest is not None and original != expected_digest:
                raise HTTPException(409, "Spotify credentials changed. Start the connection again.")
            if original is not None and not body.replace_existing:
                raise HTTPException(
                    409, "Existing Spotify credentials require explicit replacement confirmation."
                )
            services.connect_confirmation = None
            try:
                receiver = LoopbackCallbackReceiver(services.settings.spotify_redirect_uri)
            except ValueError as error:
                raise HTTPException(
                    400, "Invalid Spotify loopback redirect URI in configuration."
                ) from error
            session, url = services.auth.begin_authorization()
            ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            services.connect_status = "pending"

            async def complete() -> None:
                try:
                    callback = await receiver.receive(ready=ready)

                    def check_unchanged() -> None:
                        if credential_digest() != original:
                            raise SpotifyAuthorizationError(
                                "Credentials changed during authorization"
                            )

                    await services.auth.exchange_callback(
                        session, callback, before_save=check_unchanged
                    )
                    services.connect_status = "connected"
                except asyncio.CancelledError:
                    services.connect_status = "cancelled"
                    raise
                except Exception:
                    services.connect_status = "failed"

            task = asyncio.create_task(complete())
            services.connect_task = task
            await asyncio.wait({ready, task}, return_when=asyncio.FIRST_COMPLETED)
            if not ready.done():
                raise HTTPException(
                    503, "Could not start Spotify callback listener. Check the port and try again."
                )
            return {"authorization_url": url}

    @app.post("/api/spotify/connect")
    async def spotify_connect(body: SpotifyConnectInput) -> dict[str, str]:
        return await start_spotify_connect(body)

    @app.post("/api/spotify/connect/cancel")
    async def spotify_connect_cancel() -> dict[str, bool]:
        async with services.connect_lock:
            services.connect_confirmation = None
            if services.connect_task and not services.connect_task.done():
                services.connect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await services.connect_task
            return {"cancelled": True}

    @app.post("/api/chat")
    async def chat(body: ChatInput) -> object:
        async with services.chat_lock:
            return await handle_chat(body)

    async def handle_chat(body: ChatInput) -> object:
        text = body.message.casefold().strip().rstrip(".!?")
        if text in {"list devices", "show devices", "active devices"}:
            devices = await services.spotify.devices()
            return {
                "devices": [
                    {
                        "name": d.name,
                        "kind": d.kind,
                        "is_active": d.is_active,
                        "is_restricted": d.is_restricted,
                        "selectable": d.id is not None,
                    }
                    for d in devices
                ]
            }
        if text in SPOTIFY_CONNECT_STATUS_COMMANDS:
            status = await spotify_connect_status()
            confirmation = services.connect_confirmation
            if confirmation is not None and confirmation[1] > time.monotonic():
                status["status"] = "confirmation_required"
            return {"spotify_connection": status}
        if text in SPOTIFY_CONNECT_CANCEL_COMMANDS:
            await spotify_connect_cancel()
            return {"spotify_connection": {"status": services.connect_status, "cancelled": True}}
        if text in SPOTIFY_CONNECT_COMMANDS:
            async with services.connect_lock:
                if (
                    services.device_confirmation
                    and services.device_confirmation[2] > time.monotonic()
                ):
                    raise HTTPException(409, "Resolve the pending device confirmation first.")
                if not services.settings.spotify_client_id:
                    raise HTTPException(
                        400, "Set TUNETRAIL_SPOTIFY_CLIENT_ID in .env and restart TuneTrail."
                    )
                if services.connect_task and not services.connect_task.done():
                    return {"spotify_connection": {"status": "pending"}}
                original = credential_digest()
                if original is not None:
                    services.connect_confirmation = (original, time.monotonic() + 300)
                    return {
                        "spotify_connection": {
                            "status": "confirmation_required",
                            "message": (
                                "A credential file already exists. To authorize replacing it, "
                                "send: confirm replace Spotify credentials. "
                                "To cancel, send: cancel Spotify connection."
                            ),
                        }
                    }
            result = await start_spotify_connect(SpotifyConnectInput())
            return {"spotify_connection": {"status": "pending", **result}}
        if text == SPOTIFY_REPLACE_CONFIRMATION:
            async with services.connect_lock:
                confirmation = services.connect_confirmation
                services.connect_confirmation = None
                if confirmation is None or confirmation[1] <= time.monotonic():
                    raise HTTPException(
                        409, "No current confirmation. Start Spotify connection again."
                    )
                expected_digest = confirmation[0]
            result = await start_spotify_connect(
                SpotifyConnectInput(replace_existing=True), expected_digest=expected_digest
            )
            return {"spotify_connection": {"status": "pending", **result}}
        command = PLAYER_COMMANDS.get(text)
        if command:
            action, method, path = command
            try:
                approval = services.spotify.approvals.propose(
                    PlayerAction.create(method, path, {}, None)
                )
                services.spotify.approvals.approve(approval)
                method_name = {"next": "next_track", "previous": "previous_track"}.get(
                    action, action
                )
                await getattr(services.spotify, method_name)(approval=approval)
            except (
                SpotifyApiError,
                SpotifyAuthorizationError,
                httpx.HTTPError,
                ValueError,
            ) as error:
                raise HTTPException(
                    502,
                    "Spotify control failed or its status could not be confirmed. "
                    "Check Spotify, your connection, Premium, and the active device before "
                    "retrying; the request may have succeeded.",
                ) from error
            return {"control": action, "requested": True}
        try:
            parser = (
                services.parser
                or OpenRouterLLMFactory(services.settings).create_preference_parser()
            )
            interpretation = await services.profile.interpret_chat(body.message, parser)
        except LLMConfigurationError as error:
            raise HTTPException(503, str(error)) from error
        except IntentParseError as error:
            raise HTTPException(
                502,
                "Chat provider returned an invalid interpretation. "
                "Try rephrasing or check the configured model.",
            ) from error
        except ValueError:
            raise
        except Exception as error:
            raise HTTPException(
                502,
                "Chat provider failed or returned invalid output. "
                "Check configuration and try rephrasing.",
            ) from error

        if interpretation.spotify_action:
            action = interpretation.spotify_action
            if action.action == "list_devices":
                return await handle_chat(ChatInput(message="list devices"))
            if action.action in ("connect", "connection_status"):
                command_text = (
                    "connect spotify" if action.action == "connect" else "spotify connection status"
                )
                return await handle_chat(ChatInput(message=command_text))
            if action.action == "select_device":
                name = action.device_name
                if not name or name.casefold() not in body.message.casefold():
                    return {"clarification": "Please specify the device name exactly as shown."}
                async with services.device_lock:
                    if (
                        services.connect_confirmation
                        and services.connect_confirmation[1] > time.monotonic()
                    ):
                        raise HTTPException(
                            409, "Resolve the pending Spotify credential confirmation first."
                        )
                    services.device_confirmation = None
                    matches = [
                        d
                        for d in await services.spotify.devices()
                        if d.name.casefold() == name.casefold()
                    ]
                    if len(matches) != 1:
                        return {
                            "clarification": "That device name is unavailable or ambiguous. "
                            "List devices and specify a unique name."
                        }
                    device = matches[0]
                    if device.is_restricted or not device.id:
                        return {
                            "clarification": "That device cannot be selected. "
                            "Choose another available device."
                        }
                    if device.is_active:
                        return {"device_message": f"{device.name} is already active."}
                    services.device_confirmation = (device.id, device.name, time.monotonic() + 300)
                    return {
                        "device_message": f"Transfer Spotify playback to {device.name}? "
                        "Confirm or cancel. This confirmation expires in five minutes."
                    }
            if action.action == "cancel":
                async with services.device_lock:
                    had_device = services.device_confirmation is not None
                    services.device_confirmation = None
                if had_device:
                    return {"device_message": "Device transfer cancelled."}
                return await handle_chat(ChatInput(message="cancel spotify connection"))
            if action.action == "confirm":
                async with services.device_lock:
                    pending = services.device_confirmation
                    services.device_confirmation = None
                    if pending:
                        device_id, device_name, expires = pending
                        if expires <= time.monotonic():
                            raise HTTPException(
                                409, "Device confirmation expired. Select the device again."
                            )
                        available = [
                            d
                            for d in await services.spotify.devices()
                            if d.id == device_id and d.name == device_name and not d.is_restricted
                        ]
                        if len(available) != 1:
                            raise HTTPException(
                                409,
                                "Device changed or is unavailable. List devices and select again.",
                            )
                        request_body = {"device_ids": [device_id], "play": False}
                        approval = services.spotify.approvals.propose(
                            PlayerAction.create("PUT", "/me/player", None, request_body)
                        )
                        services.spotify.approvals.approve(approval)
                        try:
                            await services.spotify.select_device(device_id, approval=approval)
                        except (
                            SpotifyApiError,
                            SpotifyAuthorizationError,
                            httpx.HTTPError,
                            ValueError,
                        ) as error:
                            raise HTTPException(
                                502,
                                "Device transfer could not be confirmed and may have succeeded. "
                                "Check Spotify before trying again.",
                            ) from error
                        return {
                            "device_message": f"Transfer to {device_name} requested. "
                            "Check Spotify for the active device.",
                            "device_transfer_requested": True,
                        }
                if services.connect_confirmation:
                    return await handle_chat(ChatInput(message=SPOTIFY_REPLACE_CONFIRMATION))
                raise HTTPException(409, "There is no pending confirmation.")

        if interpretation.recent_plays:
            query = interpretation.recent_plays
            plays = await services.spotify.recent_plays()
            window = {"last_24_hours": timedelta(hours=24), "last_7_days": timedelta(days=7)}
            cutoff = datetime.now(UTC) - window[query.period] if query.period in window else None
            matching = [
                play
                for play in plays
                if cutoff is None
                or (isinstance(play["played_at"], datetime) and play["played_at"] >= cutoff)
            ]
            return {
                "recent_plays": matching[: query.limit],
                "period": query.period,
                "notice": (
                    "Spotify returned at most 50 recent plays. Results may not cover the "
                    "entire requested period and may be further limited for display."
                ),
            }
        return interpretation

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

    @app.get("/api/catalog-search")
    async def catalog_search(
        q: str = Query(min_length=1, max_length=200),
        kind: CatalogItemKind = CatalogItemKind.TRACK,
    ) -> object:
        if kind not in (CatalogItemKind.TRACK, CatalogItemKind.ARTIST):
            raise HTTPException(400, "Only track and artist search are supported")
        return await services.spotify.search_catalog(q, kind=kind, limit=10)

    @app.get("/api/devices")
    async def devices() -> object:
        return await services.spotify.devices()

    @app.get("/api/now-playing")
    async def now_playing() -> dict[str, object] | None:
        """Return only the display fields needed by the local player view."""
        playback = await services.spotify.current_playback()
        if playback is None or playback.track is None:
            return None
        return {
            "title": playback.track.title,
            "artists": playback.track.artists,
            "duration_ms": playback.track.duration_ms,
            "progress_ms": playback.progress_ms,
            "is_playing": playback.is_playing,
        }

    @app.post("/api/discovery")
    async def discover(body: DiscoveryInput) -> object:
        async with services.discovery_lock:
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

    # The frontend is built locally, then served from the API's protected origin.
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if (frontend / "index.html").is_file() and (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app
