"""Typed public Last.fm client for optional, bounded enrichment reads."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

import httpx

from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.lastfm.errors import (
    LastfmApiError,
    LastfmPayloadError,
    LastfmProfileError,
    LastfmRateLimitError,
    LastfmUnavailableError,
)

Sleep = Callable[[float], Awaitable[None]]
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_CONCURRENT_REQUESTS = 4
_MAX_RETRY_DELAY = 5.0


class LastfmClient:
    """Read public Last.fm data without storing a Last.fm session or raw responses."""

    def __init__(
        self,
        *,
        api_key: str,
        username: str,
        http_client: httpx.AsyncClient,
        api_url: str = "https://ws.audioscrobbler.com/2.0/",
        max_get_attempts: int = 3,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not api_key.strip() or not username.strip():
            raise ValueError("Last.fm API key and username must both be configured")
        if max_get_attempts < 1:
            raise ValueError("Last.fm max_get_attempts must be positive")
        self._api_key = api_key
        self._username = username
        self._http_client = http_client
        self._api_url = api_url
        self._max_get_attempts = max_get_attempts
        self._sleep = sleep
        self._request_slots = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)

    async def user_top_artists(self, *, limit: int = 20) -> Sequence[str]:
        """Return a bounded configured user's most-played Last.fm artists."""
        payload = await self._request("user.gettopartists", limit=limit)
        artists = _values(_nested(payload, "topartists"), "artist")
        return [_text(_mapping(value, "artist"), "name") for value in artists[:limit]]

    async def user_top_tracks(self, *, limit: int = 20) -> Sequence[Candidate]:
        """Return a bounded configured user's most-played Last.fm track suggestions."""
        payload = await self._request("user.gettoptracks", limit=limit)
        return _track_candidates(_values(_nested(payload, "toptracks"), "track")[:limit])

    async def user_recent_tracks(self, *, limit: int = 20) -> Sequence[Candidate]:
        """Return a bounded configured user's recent Last.fm track suggestions."""
        payload = await self._request("user.getrecenttracks", limit=limit)
        return _track_candidates(_values(_nested(payload, "recenttracks"), "track")[:limit])

    async def similar_tracks(
        self, *, artist: str, title: str, limit: int = 10
    ) -> Sequence[Candidate]:
        """Return bounded community-similar Last.fm tracks for one seed track."""
        payload = await self._request("track.getsimilar", artist=artist, track=title, limit=limit)
        return _track_candidates(
            _values(_nested(payload, "similartracks"), "track")[:limit], similar=True
        )

    async def similar_artists(self, artist: str, *, limit: int = 5) -> Sequence[str]:
        """Return bounded community-similar artists for one artist seed."""
        payload = await self._request("artist.getsimilar", artist=artist, limit=limit)
        artists = _values(_nested(payload, "similarartists"), "artist")
        return [_text(_mapping(value, "artist"), "name") for value in artists[:limit]]

    async def artist_top_tracks(self, artist: str, *, limit: int = 3) -> Sequence[Candidate]:
        """Return a few Last.fm top tracks for a similar-artist expansion seed."""
        payload = await self._request("artist.gettoptracks", artist=artist, limit=limit)
        return _track_candidates(_values(_nested(payload, "toptracks"), "track")[:limit])

    async def track_tags(self, *, artist: str, title: str, limit: int = 5) -> Sequence[str]:
        """Return normalized community tags for a Last.fm track."""
        payload = await self._request("track.gettoptags", artist=artist, track=title, limit=limit)
        return _tag_names(_nested(payload, "toptags"))[:limit]

    async def artist_tags(self, artist: str, *, limit: int = 5) -> Sequence[str]:
        """Return normalized community tags for a Last.fm artist."""
        payload = await self._request("artist.gettoptags", artist=artist, limit=limit)
        return _tag_names(_nested(payload, "toptags"))[:limit]

    async def _request(self, method: str, **params: str | int) -> Mapping[str, object]:
        """Call one read-only Last.fm method with safe bounded retries and error mapping."""
        request_params: dict[str, str | int] = {
            "method": method,
            "api_key": self._api_key,
            "user": self._username,
            "format": "json",
            **params,
        }
        for attempt in range(self._max_get_attempts):
            try:
                async with self._request_slots:
                    response = await self._http_client.get(self._api_url, params=request_params)
            except httpx.HTTPError as error:
                if attempt < self._max_get_attempts - 1:
                    await self._sleep(float(2**attempt))
                    continue
                raise LastfmUnavailableError(
                    "Last.fm is unavailable; Spotify-only results remain available."
                ) from error
            if (
                response.status_code in _RETRYABLE_STATUS_CODES
                and attempt < self._max_get_attempts - 1
            ):
                delay = _retry_after(response, attempt)
                if delay > _MAX_RETRY_DELAY:
                    raise _http_error(response)
                await self._sleep(delay)
                continue
            if response.is_error:
                raise _http_error(response)
            payload = _response_mapping(response)
            error = _lastfm_error(payload, method)
            if error is None:
                return payload
            if isinstance(error, LastfmRateLimitError) and attempt < self._max_get_attempts - 1:
                await self._sleep(float(2**attempt))
                continue
            raise error
        raise AssertionError("Last.fm request loop exhausted unexpectedly")


def _track_candidates(values: Sequence[object], *, similar: bool = False) -> list[Candidate]:
    """Map Last.fm track objects to non-playable provenance-bearing candidate suggestions."""
    candidates: list[Candidate] = []
    for value in values:
        track = _mapping(value, "track")
        title = _text(track, "name")
        artist = _artist_name(track)
        match = _similarity(track) if similar else 0.8
        url = _optional_text(track.get("url"))
        provenance = {"lastfm_method": "track.getsimilar" if similar else "user_or_artist_track"}
        if url is not None:
            provenance["lastfm_url"] = url
        candidates.append(
            Candidate(
                source=CandidateSource.LASTFM,
                source_id=_optional_text(track.get("mbid")) or f"{artist}:{title}",
                title=title,
                artists=[artist],
                duration_ms=_milliseconds(track.get("duration")),
                source_confidence=match,
                provenance=provenance,
            )
        )
    return candidates


def _artist_name(track: Mapping[str, object]) -> str:
    """Extract an artist name from either Last.fm's string or nested artist response shape."""
    value = track.get("artist")
    if isinstance(value, str) and value.strip():
        return value.strip()
    artist = _mapping(value, "track artist")
    return _optional_text(artist.get("name")) or _text(artist, "#text")


def _tag_names(payload: Mapping[str, object]) -> list[str]:
    """Extract ordered non-empty tag names from a Last.fm top-tags response."""
    return [_text(_mapping(value, "tag"), "name") for value in _values(payload, "tag")]


def _similarity(track: Mapping[str, object]) -> float:
    """Convert Last.fm's optional similarity match to a bounded source-confidence score."""
    value = track.get("match")
    try:
        return min(max(float(value) if isinstance(value, str | int | float) else 0.5, 0.0), 1.0)
    except ValueError:
        return 0.5


def _milliseconds(value: object) -> int | None:
    """Convert Last.fm's optional duration string to positive milliseconds when trustworthy."""
    try:
        duration = int(value) if isinstance(value, str | int) else 0
    except ValueError:
        return None
    return duration if duration > 0 else None


def _response_mapping(response: httpx.Response) -> Mapping[str, object]:
    """Read a successful Last.fm JSON object without retaining its raw response body."""
    try:
        value: object = response.json()
    except ValueError as error:
        raise LastfmPayloadError("Last.fm returned invalid JSON.") from error
    return _mapping(value, "response")


def _lastfm_error(payload: Mapping[str, object], method: str) -> LastfmApiError | None:
    """Normalize Last.fm's JSON-level API errors, which may arrive with HTTP 200."""
    code = payload.get("error")
    if not isinstance(code, int):
        return None
    if code == 29:
        return LastfmRateLimitError(
            "Last.fm is rate-limiting requests; Spotify-only results remain available."
        )
    if method.startswith("user.") and code in {6, 17}:
        return LastfmProfileError("The configured Last.fm profile is unavailable or private.")
    return LastfmApiError("Last.fm could not complete that request.")


def _http_error(response: httpx.Response) -> LastfmApiError:
    """Map unsuccessful HTTP statuses without exposing request parameters or API keys."""
    if response.status_code == 429:
        return LastfmRateLimitError(
            "Last.fm is rate-limiting requests; Spotify-only results remain available."
        )
    if response.status_code in {401, 403}:
        return LastfmApiError("Last.fm rejected the configured API key.")
    return LastfmUnavailableError("Last.fm is unavailable; Spotify-only results remain available.")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Use a positive Retry-After response when present, otherwise bounded exponential backoff."""
    try:
        return max(float(response.headers.get("Retry-After", str(2**attempt))), 0.0)
    except ValueError:
        return float(2**attempt)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    """Narrow one external JSON value to an object mapping with a safe schema error."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise LastfmPayloadError(f"Last.fm {label} response was malformed.")


def _nested(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Read a required named Last.fm response object."""
    return _mapping(payload.get(key), key)


def _values(payload: Mapping[str, object], key: str) -> Sequence[object]:
    """Narrow a Last.fm response list while accepting its one-item object variation."""
    value = payload.get(key, [])
    if isinstance(value, list):
        return cast(list[object], value)
    if isinstance(value, Mapping):
        return [cast(object, value)]
    raise LastfmPayloadError(f"Last.fm {key} response was malformed.")


def _text(payload: Mapping[str, object], key: str) -> str:
    """Read a required non-empty text field from a Last.fm JSON object."""
    value = _optional_text(payload.get(key))
    if value is None:
        raise LastfmPayloadError(f"Last.fm response omitted {key}.")
    return value


def _optional_text(value: object) -> str | None:
    """Narrow optional external text fields without treating whitespace as a usable value."""
    return value.strip() if isinstance(value, str) and value.strip() else None
