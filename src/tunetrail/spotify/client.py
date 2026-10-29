"""Async Spotify Web API client with normalized errors and safe retries."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, cast

import httpx

from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.domain.models import (
    Candidate,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    PlaybackState,
    ResolvedTrack,
)
from tunetrail.spotify.approval import PlayerAction, PlayerApprovals
from tunetrail.spotify.errors import (
    SpotifyApiError,
    SpotifyAuthenticationError,
    SpotifyNoActiveDeviceError,
    SpotifyPermissionError,
    SpotifyRateLimitError,
    SpotifyUnavailableError,
)
from tunetrail.spotify.models import (
    SpotifyPayloadError,
    SpotifyUser,
    catalog_item_from_album,
    catalog_item_from_artist,
    catalog_item_from_playlist,
    catalog_item_from_track,
    current_queue_from_payload,
    device_from_payload,
    playback_from_payload,
    resolved_tracks_from_items,
    resolved_tracks_from_values,
)

HttpMethod = Literal["GET", "POST", "PUT"]
Sleep = Callable[[float], Awaitable[None]]
QueryValue = str | int | float | bool
QueryParams = Mapping[str, QueryValue | Sequence[QueryValue]]


class SpotifyClient:
    """Call the Spotify API using a refresh-capable OAuth authenticator."""

    def __init__(
        self,
        authenticator: SpotifyAuthenticator,
        http_client: httpx.AsyncClient,
        *,
        api_url: str = "https://api.spotify.com/v1",
        max_get_attempts: int = 3,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._authenticator = authenticator
        self._http_client = http_client
        self._api_url = api_url.rstrip("/")
        self._max_get_attempts = max_get_attempts
        self._sleep = sleep
        self._player_lock = asyncio.Lock()
        self.approvals = PlayerApprovals()
        self.last_playback: PlaybackState | None = None

    async def current_user(self) -> SpotifyUser:
        """Return the authenticated Spotify user's minimal profile."""
        response = await self._request("GET", "/me")
        payload = _response_mapping(response)
        try:
            return SpotifyUser.model_validate(payload)
        except ValueError as error:
            raise SpotifyPayloadError("Spotify current-user response was malformed") from error

    async def current_playback(self) -> PlaybackState | None:
        """Return current playback, or `None` when Spotify reports no playback."""
        response = await self._request("GET", "/me/player", allow_no_content=True)
        if response.status_code == 204:
            return None
        return playback_from_payload(_response_mapping(response))

    async def devices(self) -> Sequence[Device]:
        """Return available Spotify Connect devices."""
        payload = _response_mapping(await self._request("GET", "/me/player/devices"))
        device_values = payload.get("devices")
        if not isinstance(device_values, list):
            raise SpotifyPayloadError("Spotify devices response was malformed")
        devices = cast(list[object], device_values)
        return [device_from_payload(_mapping_or_error(device)) for device in devices]

    async def recent_plays(self) -> list[dict[str, object]]:
        """Fetch one bounded page of track plays; retain no IDs or provider URLs."""
        payload = _response_mapping(
            await self._request("GET", "/me/player/recently-played", params={"limit": 50})
        )
        values = payload.get("items")
        if not isinstance(values, list):
            raise SpotifyPayloadError("Spotify recent-play response was malformed")
        rows = cast(list[object], values)
        if len(rows) > 50:
            raise SpotifyPayloadError("Spotify recent-play response was malformed")
        plays: list[dict[str, object]] = []
        for value in rows:
            item = _mapping_or_error(value)
            track = _mapping_or_error(item.get("track"))
            title = track.get("name")
            artists = track.get("artists")
            played_at = item.get("played_at")
            if (
                not isinstance(title, str)
                or not title
                or not isinstance(artists, list)
                or not artists
            ):
                raise SpotifyPayloadError("Spotify recent-play response was malformed")
            names: list[str] = []
            for artist in cast(list[object], artists):
                name = _mapping_or_error(artist).get("name")
                if not isinstance(name, str) or not name:
                    raise SpotifyPayloadError("Spotify recent-play response was malformed")
                names.append(name)
            if not isinstance(played_at, str):
                raise SpotifyPayloadError("Spotify recent-play response was malformed")
            try:
                timestamp = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError("Missing timezone")
            except ValueError as error:
                raise SpotifyPayloadError("Spotify recent-play response was malformed") from error
            plays.append({"title": title, "artists": names, "played_at": timestamp.astimezone(UTC)})
        return plays

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Search the Spotify catalog for at most ten tracks."""
        catalog_items = await self.search_catalog(query, kind=CatalogItemKind.TRACK, limit=limit)
        return [item.track.candidates[0] for item in catalog_items if item.track is not None]

    async def search_playable_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Search with market context and retain only explicitly playable, unrestricted tracks."""
        from tunetrail.spotify.models import candidate_from_track

        if not query.strip() or not 1 <= limit <= 10:
            raise ValueError("Spotify search requires a query and a limit of 1–10")
        payload = _response_mapping(
            await self._request(
                "GET",
                "/search",
                params={"q": query, "type": "track", "limit": limit, "market": "from_token"},
            )
        )
        page = _mapping_or_error(payload.get("tracks"))
        items = page.get("items")
        if not isinstance(items, list):
            raise SpotifyPayloadError("Spotify search response was malformed")
        rows = cast(list[object], items)
        total = page.get("total")
        if (
            type(total) is not int
            or total != len(rows)
            or page.get("next") is not None
            or page.get("offset", 0) != 0
        ):
            return []  # A truncated result set cannot establish uniqueness.
        results: list[Candidate] = []
        for value in rows:
            item = _mapping_or_error(value)
            if item.get("is_playable") is True and not item.get("restrictions"):
                results.append(candidate_from_track(item))
        return results

    async def search_catalog(
        self, query: str, *, kind: CatalogItemKind, limit: int = 10
    ) -> Sequence[CatalogItem]:
        """Search one explicit Spotify catalog resource type for at most ten results."""
        if not query.strip():
            raise ValueError("Spotify catalog search query cannot be empty")
        if not 1 <= limit <= 10:
            raise ValueError("Spotify catalog search limit must be between 1 and 10")
        payload = _response_mapping(
            await self._request(
                "GET", "/search", params={"q": query, "type": kind.value, "limit": limit}
            )
        )
        results = _mapping_or_error(payload.get(f"{kind.value}s"))
        items = results.get("items")
        if not isinstance(items, list):
            raise SpotifyPayloadError("Spotify search response was malformed")
        mapper = {
            CatalogItemKind.TRACK: catalog_item_from_track,
            CatalogItemKind.ARTIST: catalog_item_from_artist,
            CatalogItemKind.ALBUM: catalog_item_from_album,
            CatalogItemKind.PLAYLIST: catalog_item_from_playlist,
        }[kind]
        return [mapper(_mapping_or_error(item)) for item in cast(list[object], items)]

    async def tracks_for_catalog_item(
        self, item: CatalogItem, *, limit: int = 10
    ) -> Sequence[ResolvedTrack]:
        """Return tracks for a selected catalog result without changing playback."""
        if not 1 <= limit <= 50:
            raise ValueError("Spotify context track limit must be between 1 and 50")
        if item.kind is CatalogItemKind.TRACK:
            return [item.track] if item.track is not None else []
        if item.kind is CatalogItemKind.ARTIST:
            payload = _response_mapping(
                await self._request(
                    "GET",
                    f"/artists/{item.spotify_id}/top-tracks",
                    params={"market": "from_token"},
                )
            )
            values = payload.get("tracks")
            if not isinstance(values, list):
                raise SpotifyPayloadError("Spotify artist tracks response was malformed")
            return resolved_tracks_from_values(cast(list[object], values))[:limit]
        if item.kind is CatalogItemKind.ALBUM:
            payload = _response_mapping(
                await self._request(
                    "GET", f"/albums/{item.spotify_id}/tracks", params={"limit": limit}
                )
            )
            return resolved_tracks_from_items(payload)
        payload = _response_mapping(
            await self._request(
                "GET", f"/playlists/{item.spotify_id}/items", params={"limit": limit}
            )
        )
        return resolved_tracks_from_items(payload)

    async def current_queue(self) -> CurrentQueue | None:
        """Return the current item and upcoming tracks visible to Spotify."""
        response = await self._request("GET", "/me/player/queue", allow_no_content=True)
        if response.status_code == 204:
            return None
        return current_queue_from_payload(_response_mapping(response))

    async def add_to_queue(
        self, spotify_uri: str, *, device_id: str | None = None, approval: str | None = None
    ) -> None:
        """Append one explicit Spotify track URI to the selected device queue."""
        await self._player_request(
            "POST",
            "/me/player/queue",
            params=_device_params({"uri": spotify_uri}, device_id),
            approval=approval,
        )

    async def start_playback(
        self,
        spotify_uris: Sequence[str],
        *,
        device_id: str | None = None,
        approval: str | None = None,
    ) -> None:
        """Start playback of explicit Spotify track URIs on the selected device."""
        if not spotify_uris:
            raise ValueError("At least one Spotify URI is required to start playback")
        await self._player_request(
            "PUT",
            "/me/player/play",
            params=_device_params({}, device_id),
            json={"uris": list(spotify_uris)},
            approval=approval,
        )

    async def resume(self, *, device_id: str | None = None, approval: str | None = None) -> None:
        """Resume current playback on the selected device."""
        await self._player_request(
            "PUT", "/me/player/play", params=_device_params({}, device_id), approval=approval
        )

    async def pause(self, *, device_id: str | None = None, approval: str | None = None) -> None:
        """Pause playback on the selected device."""
        await self._player_request(
            "PUT", "/me/player/pause", params=_device_params({}, device_id), approval=approval
        )

    async def next_track(
        self, *, device_id: str | None = None, approval: str | None = None
    ) -> None:
        """Skip to the next track on the selected device."""
        await self._player_request(
            "POST", "/me/player/next", params=_device_params({}, device_id), approval=approval
        )

    async def previous_track(
        self, *, device_id: str | None = None, approval: str | None = None
    ) -> None:
        """Return to the previous track on the selected device."""
        await self._player_request(
            "POST", "/me/player/previous", params=_device_params({}, device_id), approval=approval
        )

    async def select_device(
        self, device_id: str, *, play: bool = False, approval: str | None = None
    ) -> None:
        """Transfer playback control to a visible Spotify device."""
        await self._player_request(
            "PUT", "/me/player", json={"device_ids": [device_id], "play": play}, approval=approval
        )

    async def _player_request(
        self,
        method: Literal["POST", "PUT"],
        path: str,
        *,
        params: QueryParams | None = None,
        json: Mapping[str, object] | None = None,
        approval: str | None = None,
    ) -> None:
        """Serialize player mutations and refresh visible state after each one."""
        async with self._player_lock:
            await self._request(
                method, path, params=params, json=json, allow_no_content=True, approval=approval
            )
            self.last_playback = await self.current_playback()

    async def _request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: QueryParams | None = None,
        json: Mapping[str, object] | None = None,
        allow_no_content: bool = False,
        approval: str | None = None,
    ) -> httpx.Response:
        """Make an authorized request, safely retrying transient GET failures."""
        if method != "GET" and (path == "/me/player" or path.startswith("/me/player/")):
            self.approvals.consume(approval, PlayerAction.create(method, path, params, json))
        attempts = self._max_get_attempts if method == "GET" else 1
        for attempt in range(attempts):
            token = await self._authenticator.access_token()
            response = await self._http_client.request(
                method,
                f"{self._api_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json,
            )
            if response.status_code == 429 and attempt < attempts - 1:
                await self._sleep(_retry_after(response))
                continue
            if response.status_code in {500, 502, 503, 504} and attempt < attempts - 1:
                await self._sleep(float(2**attempt))
                continue
            if response.is_error or (response.status_code == 204 and not allow_no_content):
                raise _spotify_error(response)
            return response
        raise AssertionError("Spotify request loop exhausted unexpectedly")


def _response_mapping(response: httpx.Response) -> Mapping[str, object]:
    """Read a Spotify JSON object or reject a malformed successful response."""
    try:
        payload = response.json()
    except ValueError as error:
        raise SpotifyPayloadError("Spotify response was not JSON") from error
    return _mapping_or_error(payload)


def _mapping_or_error(value: object) -> Mapping[str, object]:
    """Narrow arbitrary JSON values to a typed object mapping."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise SpotifyPayloadError("Spotify response was not a JSON object")


def _device_params(
    params: QueryParams, device_id: str | None
) -> dict[str, QueryValue | Sequence[QueryValue]]:
    """Add an optional selected-device query parameter to an API request."""
    result = dict(params)
    if device_id is not None:
        result["device_id"] = device_id
    return result


def _retry_after(response: httpx.Response) -> float:
    """Return Spotify's retry delay, with a conservative fallback for invalid headers."""
    try:
        return max(float(response.headers.get("Retry-After", "1")), 0.0)
    except ValueError:
        return 1.0


def _spotify_error(response: httpx.Response) -> SpotifyApiError:
    """Normalize Spotify status responses into safe, actionable exception types."""
    message = _spotify_message(response)
    if response.status_code == 401:
        return SpotifyAuthenticationError(
            401, "Spotify authorization expired or was revoked. Sign in again."
        )
    if response.status_code == 403:
        return SpotifyPermissionError(403, message)
    if response.status_code == 404 and "no active device" in message.lower():
        return SpotifyNoActiveDeviceError(404, "No active Spotify device is available.")
    if response.status_code == 404:
        return SpotifyUnavailableError(404, message)
    if response.status_code == 429:
        return SpotifyRateLimitError(message, _retry_after(response))
    return SpotifyApiError(response.status_code, message)


def _spotify_message(response: httpx.Response) -> str:
    """Extract Spotify's API error message without exposing request credentials."""
    try:
        payload_object: object = response.json()
    except ValueError:
        return f"Spotify request failed with HTTP {response.status_code}"
    if not isinstance(payload_object, dict):
        return f"Spotify request failed with HTTP {response.status_code}"
    payload = cast(dict[str, object], payload_object)
    error = payload.get("error")
    if isinstance(error, dict):
        error_payload = cast(dict[str, object], error)
        message = error_payload.get("message")
        if isinstance(message, str):
            return message
    return f"Spotify request failed with HTTP {response.status_code}"
