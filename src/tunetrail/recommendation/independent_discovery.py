"""Bounded MusicBrainz discovery from explicitly independent identifiers only.

This interface deliberately does not accept legacy Candidate or Spotify objects.
The caller is responsible for truthfully supplying independently obtained MBIDs.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from tunetrail.musicbrainz.client import MusicBrainzError
from tunetrail.musicbrainz.models import Recording


class SeedKind(StrEnum):
    """MusicBrainz entities supported by the bounded first discovery pass."""

    ARTIST = "artist"
    RECORDING = "recording"


@dataclass(frozen=True, slots=True)
class IndependentSeed:
    """An owner-supplied MusicBrainz ID, independently obtained, not translated from Spotify."""

    kind: SeedKind
    musicbrainz_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.kind), SeedKind) or not isinstance(
            cast(object, self.musicbrainz_id), UUID
        ):
            raise ValueError("Independent seeds require a seed kind and MusicBrainz UUID")


@dataclass(frozen=True, slots=True)
class IndependentCandidate:
    """Minimal MusicBrainz result; no Spotify-derived metadata or guessed features."""

    recording_id: UUID
    title: str
    artists: tuple[str, ...]
    duration_ms: int | None
    source: str = "musicbrainz"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Candidates and explicit limitations rather than inferred matches."""

    candidates: tuple[IndependentCandidate, ...]
    source_failures: int
    needs_more_seeds: bool
    enrichment_status: str = "unavailable: independent ReccoBeats lookup not established"


class IndependentMusicBrainz(Protocol):
    """Only UUID-based operations are available to the discovery flow."""

    async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]: ...

    async def recording(self, recording_id: UUID) -> Recording: ...


class IndependentDiscovery:
    """One bounded request per seed; no namesake guessing or provider translation."""

    def __init__(self, source: IndependentMusicBrainz, *, timeout_seconds: float = 10) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Timeout must be positive and no greater than 30 seconds")
        self._source = source
        self._timeout = timeout_seconds

    async def discover(
        self, seeds: Sequence[IndependentSeed], *, limit: int = 10
    ) -> DiscoveryResult:
        """Return up to 20 results; names without an MBID need clarification."""
        if type(limit) is not int or not 1 <= limit <= 20 or len(seeds) > 5:
            raise ValueError("Discovery allows at most five seeds and 1–20 results")
        if any(type(seed) is not IndependentSeed for seed in seeds):
            raise ValueError("Discovery requires independently supplied MusicBrainz seeds")
        candidates: dict[UUID, IndependentCandidate] = {}
        failures = 0
        for seed in dict.fromkeys(seeds):
            if len(candidates) >= limit:
                break
            try:
                async with asyncio.timeout(self._timeout):
                    if seed.kind is SeedKind.ARTIST:
                        recordings = await self._source.browse_artist_recordings(
                            seed.musicbrainz_id, limit=limit - len(candidates)
                        )
                    else:
                        recordings = [await self._source.recording(seed.musicbrainz_id)]
            except (MusicBrainzError, TimeoutError):
                failures += 1
                continue
            for recording in recordings[: limit - len(candidates)]:
                candidate = _candidate(recording, seed)
                if candidate is not None:
                    candidates.setdefault(candidate.recording_id, candidate)
        return DiscoveryResult(tuple(candidates.values()), failures, len(candidates) < limit)


def _candidate(recording: Recording, seed: IndependentSeed) -> IndependentCandidate | None:
    try:
        recording_id = UUID(recording.id)
        artist_ids = [UUID(artist.id) for artist in recording.artists]
    except ValueError:
        return None
    if seed.kind is SeedKind.ARTIST and seed.musicbrainz_id not in artist_ids:
        return None
    if seed.kind is SeedKind.RECORDING and seed.musicbrainz_id != recording_id:
        return None
    title = recording.title.strip()
    artists = tuple(artist.name.strip() for artist in recording.artists)
    if not title or not artists or any(not name for name in artists):
        return None
    duration = (
        recording.duration_ms if recording.duration_ms and recording.duration_ms > 0 else None
    )
    return IndependentCandidate(recording_id, title, artists, duration)
