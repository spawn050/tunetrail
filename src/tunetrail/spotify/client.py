"""Async Spotify Web API client with normalized errors and safe retries."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Literal, cast

import httpx

from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.domain.models import Candidate, Device, PlaybackState
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
    candidate_from_track,
    device_from_payload,
    playback_from_payload,
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

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Search the Spotify catalog for at most ten tracks."""
        if not 1 <= limit <= 10:
            raise ValueError("Spotify track search limit must be between 1 and 10")
        payload = _response_mapping(
            await self._request(
                "GET", "/search", params={"q": query, "type": "track", "limit": limit}
            )
        )
        tracks = _mapping_or_error(payload.get("tracks"))
        items = tracks.get("items")
        if not isinstance(items, list):
            raise SpotifyPayloadError("Spotify search response was malformed")
        track_items = cast(list[object], items)
        return [candidate_from_track(_mapping_or_error(item)) for item in track_items]

    async def add_to_queue(self, spotify_uri: str, *, device_id: str | None = None) -> None:
        """Append one explicit Spotify track URI to the selected device queue."""
        await self._player_request(
            "POST",
            "/me/player/queue",
            params=_device_params({"uri": spotify_uri}, device_id),
        )

    async def start_playback(
        self, spotify_uris: Sequence[str], *, device_id: str | None = None
    ) -> None:
        """Start playback of explicit Spotify track URIs on the selected device."""
        if not spotify_uris:
            raise ValueError("At least one Spotify URI is required to start playback")
        await self._player_request(
            "PUT",
            "/me/player/play",
            params=_device_params({}, device_id),
            json={"uris": list(spotify_uris)},
        )

    async def resume(self, *, device_id: str | None = None) -> None:
        """Resume current playback on the selected device."""
        await self._player_request("PUT", "/me/player/play", params=_device_params({}, device_id))

    async def pause(self, *, device_id: str | None = None) -> None:
        """Pause playback on the selected device."""
        await self._player_request("PUT", "/me/player/pause", params=_device_params({}, device_id))

    async def next_track(self, *, device_id: str | None = None) -> None:
        """Skip to the next track on the selected device."""
        await self._player_request("POST", "/me/player/next", params=_device_params({}, device_id))

    async def previous_track(self, *, device_id: str | None = None) -> None:
        """Return to the previous track on the selected device."""
        await self._player_request(
            "POST", "/me/player/previous", params=_device_params({}, device_id)
        )

    async def select_device(self, device_id: str, *, play: bool = False) -> None:
        """Transfer playback control to a visible Spotify device."""
        await self._player_request(
            "PUT", "/me/player", json={"device_ids": [device_id], "play": play}
        )

    async def _player_request(
        self,
        method: Literal["POST", "PUT"],
        path: str,
        *,
        params: QueryParams | None = None,
        json: Mapping[str, object] | None = None,
    ) -> None:
        """Serialize player mutations and refresh visible state after each one."""
        async with self._player_lock:
            await self._request(method, path, params=params, json=json, allow_no_content=True)
            self.last_playback = await self.current_playback()

    async def _request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: QueryParams | None = None,
        json: Mapping[str, object] | None = None,
        allow_no_content: bool = False,
    ) -> httpx.Response:
        """Make an authorized request, safely retrying transient GET failures."""
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
