"""Bounded local cache for normalized canonical recording matches."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from tunetrail.domain.models import Candidate
from tunetrail.musicbrainz.lookup import RecordingLookup, lookup_recording
from tunetrail.musicbrainz.matching import RecordingMatch

_TTL = timedelta(days=30)


class MatchStore(Protocol):
    async def musicbrainz_match(
        self, cache_key: str, *, now: datetime
    ) -> RecordingMatch | None: ...

    async def save_musicbrainz_match(
        self, cache_key: str, match: RecordingMatch, *, refreshed_at: datetime, expires_at: datetime
    ) -> None: ...


class CachedRecordingLookup:
    """Cache only uniquely matched normalized metadata, never source failures or raw responses."""

    def __init__(self, client: RecordingLookup, store: MatchStore) -> None:
        self._client = client
        self._store = store

    async def cached(self, candidate: Candidate) -> RecordingMatch | None:
        """Read a match without network access, suitable for the interactive path."""
        return await self._store.musicbrainz_match(_key(candidate), now=datetime.now(UTC))

    async def refresh(self, candidate: Candidate) -> RecordingMatch | None:
        """Perform an explicit background lookup and store a successful match."""
        match = await lookup_recording(candidate, self._client)
        if match is not None:
            now = datetime.now(UTC)
            await self._store.save_musicbrainz_match(
                _key(candidate), match, refreshed_at=now, expires_at=now + _TTL
            )
        return match


def _key(candidate: Candidate) -> str:
    """Include all matching evidence so similar editions cannot share a cached result."""
    evidence = "\0".join(
        [candidate.isrc or "", candidate.title, *candidate.artists, str(candidate.duration_ms)]
    )
    return "musicbrainz:" + sha256(evidence.casefold().encode()).hexdigest()
