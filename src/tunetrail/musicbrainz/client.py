"""Read-only MusicBrainz client with application-wide request pacing."""

import asyncio
import fcntl
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import ValidationError

from tunetrail.musicbrainz.models import Artist, Recording, Release
from tunetrail.paths import AppPaths

_USER_AGENT = "TuneTrail/0.1.0 ( https://github.com/spawn050/tunetrail )"


class MusicBrainzError(Exception):
    """A safe optional-source error without request or response contents."""


@dataclass(frozen=True, slots=True)
class ArtistMatch:
    id: UUID
    name: str
    disambiguation: str


class RateLimiter:
    """Coordinate requests by all local instances sharing the same TuneTrail cache directory."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path or AppPaths.default().cache_dir / "musicbrainz-rate.lock"

    async def get(
        self, client: httpx.AsyncClient, url: str, params: Mapping[str, str]
    ) -> httpx.Response:
        """Pace every request, including retries, across all shared client instances."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            raw = os.read(fd, 64)
            try:
                last_start = float(raw)
            except ValueError:
                last_start = 0.0
            now = time.monotonic()
            # A reboot resets the monotonic clock; conservatively wait a second.
            delay = 1.0 if last_start > now else 1.0 - (now - last_start)
            if delay > 0:
                await asyncio.sleep(delay)
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, str(time.monotonic()).encode("ascii"))
            request = httpx.Request("GET", url, params=params, headers={"User-Agent": _USER_AGENT})
            return await client.send(request, auth=None, follow_redirects=False)
        finally:
            os.close(fd)


_LIMITER = RateLimiter()


class MusicBrainzClient:
    """Bounded public metadata lookups; does not persist external responses."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        api_url: str = "https://musicbrainz.org/ws/2",
        limiter: RateLimiter = _LIMITER,
    ) -> None:
        self._http = http_client
        self._url = api_url.rstrip("/")
        self._limiter = limiter

    async def by_isrc(self, isrc: str) -> list[Recording]:
        """Look up all recordings associated with an ISRC; clashes can occur."""
        if len(isrc) != 12 or not isrc.isascii() or not isrc.isalnum():
            raise ValueError("ISRC must contain 12 ASCII letters or digits")
        payload = await self._get(
            f"isrc/{isrc.upper()}", {"inc": "artist-credits+releases+isrcs"}, allow_not_found=True
        )
        items = payload.get("recordings")
        if not isinstance(items, list):
            raise MusicBrainzError("MusicBrainz returned incomplete ISRC results.")
        item_count = len(cast(list[object], items))
        total = payload.get("recording-count")
        if item_count >= 25 or (
            total is not None and (type(total) is not int or total != item_count)
        ):
            raise MusicBrainzError("MusicBrainz returned incomplete ISRC results.")
        # The endpoint itself proves the ISRC association even if the response omits isrcs.
        return [
            recording.model_copy(
                update={"isrcs": list(dict.fromkeys([*recording.isrcs, isrc.upper()]))}
            )
            for recording in _recordings(payload)
        ]

    async def search(self, *, artist: str, title: str) -> list[Recording]:
        """Reject incomplete search results before attempting a uniqueness match."""
        query = f'recording:"{_escape(title)}" AND artist:"{_escape(artist)}"'
        payload = await self._get("recording", {"query": query, "limit": "10"})
        items = payload.get("recordings")
        count = payload.get("count")
        offset = payload.get("offset", 0)
        if not isinstance(items, list):
            raise MusicBrainzError("MusicBrainz returned incomplete search results.")
        item_count = len(cast(list[object], items))
        if (
            type(count) is not int
            or count != item_count
            or item_count > 10
            or type(offset) is not int
            or offset != 0
        ):
            raise MusicBrainzError("MusicBrainz returned incomplete search results.")
        return _recordings(payload)

    async def artist(self, mbid: str) -> Artist:
        """Fetch a canonical artist and aliases."""
        payload = await self._get(f"artist/{quote(mbid, safe='')}", {"inc": "aliases"})
        return _artist(payload)

    async def search_artists_independent(self, name: str) -> list[ArtistMatch]:
        """Find a bounded page for explicit user selection, not identity inference."""
        if not name.strip() or len(name) > 200 or re_spotify_reference(name):
            raise ValueError("Artist name must be independently supplied")
        payload = await self._get("artist", {"query": f'artist:"{_escape(name)}"', "limit": "5"})
        items = payload.get("artists")
        if not isinstance(items, list) or len(cast(list[object], items)) > 5:
            raise MusicBrainzError("MusicBrainz returned invalid artist results.")
        matches: list[ArtistMatch] = []
        for item in cast(list[object], items):
            value = _map(item)
            try:
                identifier = UUID(str(value.get("id")))
            except ValueError as error:
                raise MusicBrainzError("MusicBrainz returned invalid artist results.") from error
            artist_name = value.get("name")
            description = value.get("disambiguation", "")
            if (
                not isinstance(artist_name, str)
                or not artist_name.strip()
                or len(artist_name) > 200
                or any(ord(c) < 32 for c in artist_name)
            ):
                raise MusicBrainzError("MusicBrainz returned invalid artist results.")
            if (
                not isinstance(description, str)
                or len(description) > 300
                or any(ord(c) < 32 for c in description)
            ):
                raise MusicBrainzError("MusicBrainz returned invalid artist results.")
            if re_spotify_reference(artist_name) or re_spotify_reference(description):
                continue
            matches.append(ArtistMatch(identifier, artist_name, description))
        return matches

    async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
        """Browse one bounded page linked to an independently identified artist."""
        if not 1 <= limit <= 20:
            raise ValueError("Recording browse limit must be between 1 and 20")
        payload = await self._get(
            "recording",
            {"artist": str(artist_id), "limit": str(limit), "inc": "artist-credits"},
        )
        items = payload.get("recordings")
        count = payload.get("recording-count")
        offset = payload.get("recording-offset")
        if not isinstance(items, list):
            raise MusicBrainzError("MusicBrainz returned invalid browse results.")
        item_count = len(cast(list[object], items))
        if (
            item_count > limit
            or type(count) is not int
            or count < item_count
            or type(offset) is not int
            or offset != 0
        ):
            raise MusicBrainzError("MusicBrainz returned invalid browse results.")
        return _recordings(payload)

    async def recording(self, recording_id: UUID) -> Recording:
        """Look up an independently identified recording by MusicBrainz ID."""
        payload = await self._get(f"recording/{recording_id}", {"inc": "artist-credits"})
        recordings = _recordings({"recordings": [payload]})
        if len(recordings) != 1 or recordings[0].id.lower() != str(recording_id):
            raise MusicBrainzError("MusicBrainz returned an inconsistent recording.")
        return recordings[0]

    async def _get(
        self, path: str, params: dict[str, str], *, allow_not_found: bool = False
    ) -> Mapping[str, object]:
        """Retry one temporary 503, without logging private request details."""
        for attempt in range(2):
            try:
                response = await self._limiter.get(
                    self._http, f"{self._url}/{path}", {**params, "fmt": "json"}
                )
            except httpx.HTTPError as error:
                raise MusicBrainzError("MusicBrainz is unavailable.") from error
            if response.status_code == 503 and attempt == 0:
                delay = _retry_after(response)
                if delay > 5:
                    raise MusicBrainzError("MusicBrainz is unavailable.")
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            if response.status_code == 404 and allow_not_found:
                return {"recordings": []}
            if response.is_error:
                raise MusicBrainzError("MusicBrainz is unavailable.")
            try:
                payload: object = response.json()
            except ValueError as error:
                raise MusicBrainzError("MusicBrainz returned invalid data.") from error
            if not isinstance(payload, dict):
                raise MusicBrainzError("MusicBrainz returned invalid data.")
            return cast(Mapping[str, object], payload)
        raise AssertionError("unreachable")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def re_spotify_reference(value: str) -> bool:
    return "spotify:" in value.casefold() or "spotify.com" in value.casefold()


def _retry_after(response: httpx.Response) -> float:
    """Respect numeric and HTTP-date Retry-After values without indefinite waits."""
    header = response.headers.get("Retry-After")
    if header is None:
        return 0.0
    try:
        value = float(header)
        return max(value, 0.0) if math.isfinite(value) else math.inf
    except ValueError:
        try:
            when = parsedate_to_datetime(header)
            if when.tzinfo is None:
                return math.inf
            return max((when - datetime.now(UTC)).total_seconds(), 0.0)
        except (TypeError, ValueError, OverflowError):
            return math.inf


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _map(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, dict) else {}


def _artist(value: Mapping[str, object]) -> Artist:
    try:
        return Artist.model_validate(
            {
                "id": value.get("id"),
                "name": value.get("name"),
                "aliases": [
                    alias["name"]
                    for item in _list(value.get("aliases"))
                    if isinstance((alias := _map(item)).get("name"), str)
                ],
            }
        )
    except ValidationError as error:
        raise MusicBrainzError("MusicBrainz returned invalid artist data.") from error


def _recordings(payload: Mapping[str, object]) -> list[Recording]:
    recordings: list[Recording] = []
    for item in _list(payload.get("recordings"))[:25]:
        value = _map(item)
        try:
            artists = [
                _artist(artist)
                for credit in _list(value.get("artist-credit"))
                if (artist := _map(_map(credit).get("artist")))
            ]
            releases = [
                Release.model_validate(
                    {
                        "id": release.get("id"),
                        "title": release.get("title"),
                        "date": release.get("date"),
                        "release_type": _map(release.get("release-group")).get("primary-type"),
                    }
                )
                for item in _list(value.get("releases"))[:25]
                if (release := _map(item))
            ]
            recordings.append(
                Recording.model_validate(
                    {
                        "id": value.get("id"),
                        "title": value.get("title"),
                        "artists": artists,
                        "duration_ms": value.get("length"),
                        "isrcs": _list(value.get("isrcs")),
                        "releases": releases,
                        # The API may omit linked releases or return only the first page.
                        # Absence of an explicit total is not proof of completeness.
                        "releases_complete": (
                            type(value.get("release-count")) is int
                            and isinstance(value.get("releases"), list)
                            and value.get("release-count") == len(releases)
                            and len(_list(value.get("releases"))) == len(releases)
                        ),
                        "tags": [
                            tag["name"]
                            for item in _list(value.get("tags"))[:25]
                            if isinstance((tag := _map(item)).get("name"), str)
                        ],
                    }
                )
            )
        except ValidationError as error:
            raise MusicBrainzError("MusicBrainz returned invalid recording data.") from error
    return recordings
