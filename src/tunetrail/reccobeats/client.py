"""Optional, bounded ReccoBeats enrichment of independent MusicBrainz candidates."""

import asyncio
import math
import time
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast
from uuid import UUID

import httpx

from tunetrail.reccobeats.matching import match_search
from tunetrail.recommendation.independent_discovery import (
    EnrichedCandidate,
    FeatureEstimate,
    IndependentCandidate,
)

_BASE_URL = "https://api.reccobeats.com/v1"
_UNIT_FEATURES = frozenset(
    {
        "acousticness",
        "danceability",
        "energy",
        "instrumentalness",
        "liveness",
        "speechiness",
        "valence",
    }
)


class _Unavailable(Exception):
    """A safe provider failure, without leaking HTTP request details."""


class ReccoBeatsEnricher:
    """Read estimates without storing raw responses or translating Spotify metadata.

    Callers must only pass MusicBrainz-derived candidates created by the independent
    discovery flow; Python types cannot prove the provenance of arbitrary strings.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        timeout_seconds: float = 5,
        cache_size: int = 128,
        cache_ttl_seconds: float = 900,
    ) -> None:
        if (
            not 0 < timeout_seconds <= 30
            or type(cache_size) is not int
            or not 1 <= cache_size <= 128
        ):
            raise ValueError("Invalid ReccoBeats timeout or cache size")
        if not 0 < cache_ttl_seconds <= 3600:
            raise ValueError("Cache lifetime must be between 0 and 3600 seconds")
        self._http = http_client
        self._timeout = timeout_seconds
        self._cache_size = cache_size
        self._ttl = cache_ttl_seconds
        self._cache: OrderedDict[IndependentCandidate, tuple[float, FeatureEstimate]] = (
            OrderedDict()
        )
        self._lock = asyncio.Lock()
        self._retry_at = 0.0
        self._last_request = 0.0

    async def enrich(self, candidate: IndependentCandidate) -> EnrichedCandidate:
        """Return the original candidate on misses, ambiguities, or provider failure."""
        if not _valid_candidate(candidate):
            raise ValueError("ReccoBeats requires an independent MusicBrainz candidate")
        if (
            candidate.duration_ms is None
            or len(candidate.title) < 3
            or len(candidate.artists[0]) < 3
        ):
            return EnrichedCandidate(candidate, None, "insufficient independent match evidence")
        async with self._lock:
            cached = self._cache.get(candidate)
            if cached is not None:
                if cached[0] > time.monotonic():
                    self._cache.move_to_end(candidate)
                    return EnrichedCandidate(candidate, cached[1], "available")
                del self._cache[candidate]
            try:
                search = await self._get(
                    "/track/search",
                    {
                        "searchText": candidate.title,
                        "artist": candidate.artists[0],
                        "page": "0",
                        "size": "10",
                    },
                )
                track_id = match_search(candidate, search)
                if track_id is None:
                    return EnrichedCandidate(candidate, None, "no unique supported match")
                payload = await self._get(f"/track/{track_id}/audio-features", {})
                features = _features(track_id, payload)
                if features is None:
                    return EnrichedCandidate(candidate, None, "features unavailable")
            except _Unavailable:
                return EnrichedCandidate(candidate, None, "source unavailable")
            self._cache[candidate] = (time.monotonic() + self._ttl, features)
            self._cache.move_to_end(candidate)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            return EnrichedCandidate(candidate, features, "available")

    async def _get(self, path: str, params: Mapping[str, str]) -> object:
        if time.monotonic() < self._retry_at:
            raise _Unavailable
        delay = 0.25 - (time.monotonic() - self._last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            async with asyncio.timeout(self._timeout):
                self._last_request = time.monotonic()
                response = await self._http.get(
                    _BASE_URL + path, params=params, timeout=self._timeout, follow_redirects=False
                )
        except (httpx.HTTPError, TimeoutError) as error:
            raise _Unavailable from error
        if response.status_code == 429:
            self._retry_at = time.monotonic() + max(
                1.0, _retry_after(response.headers.get("Retry-After"))
            )
            raise _Unavailable
        if not 200 <= response.status_code < 300:
            raise _Unavailable
        try:
            return cast(object, response.json())
        except ValueError as error:
            raise _Unavailable from error


def _valid_candidate(candidate: IndependentCandidate) -> bool:
    if type(candidate) is not IndependentCandidate or candidate.source != "musicbrainz":
        return False
    if not isinstance(cast(object, candidate.recording_id), UUID) or not isinstance(
        cast(object, candidate.title), str
    ):
        return False
    if not isinstance(cast(object, candidate.artists), tuple) or not candidate.artists:
        return False
    values = (candidate.title, *candidate.artists)
    if any(
        not isinstance(cast(object, value), str)
        or not value.strip()
        or len(value) > 1000
        or "spotify:" in value.casefold()
        or "open.spotify.com" in value.casefold()
        for value in values
    ):
        return False
    return candidate.duration_ms is None or (
        type(candidate.duration_ms) is int and candidate.duration_ms > 0
    )


def _features(track_id: UUID, payload: object) -> FeatureEstimate | None:
    if not isinstance(payload, dict):
        return None
    data = cast(Mapping[str, object], payload)
    try:
        if not isinstance(data.get("id"), str) or UUID(cast(str, data["id"])) != track_id:
            return None
    except ValueError:
        return None
    values: list[tuple[str, int | float]] = []
    for name in sorted(_UNIT_FEATURES | {"key", "mode", "loudness", "tempo"}):
        value = data.get(name)
        if type(value) not in (int, float):
            continue
        number = cast(int | float, value)
        try:
            finite = math.isfinite(number)
        except OverflowError:
            continue
        if not finite:
            continue
        if name in _UNIT_FEATURES and not 0 <= number <= 1:
            continue
        if name == "key" and (type(number) is not int or not 0 <= number <= 11):
            continue
        if name == "mode" and (type(number) is not int or number not in (0, 1)):
            continue
        if name == "tempo" and number <= 0:
            continue
        values.append((name, number))
    return FeatureEstimate(track_id, tuple(values)) if values else None


def _retry_after(header: str | None) -> float:
    if header:
        try:
            seconds = float(header)
            return max(seconds, 0) if math.isfinite(seconds) else 60.0
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is not None:
                    return max((date - datetime.now(UTC)).total_seconds(), 0)
            except (TypeError, ValueError, OverflowError):
                pass
    return 60.0
