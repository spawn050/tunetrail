"""Conservative canonical lookup flow for already retrieved candidates."""

from collections.abc import Sequence
from typing import Protocol

from tunetrail.domain.models import Candidate
from tunetrail.musicbrainz.matching import RecordingMatch, match_recording
from tunetrail.musicbrainz.models import Recording


class RecordingLookup(Protocol):
    """Read-only portion of the MusicBrainz client required for resolution."""

    async def by_isrc(self, isrc: str) -> Sequence[Recording]: ...

    async def search(self, *, artist: str, title: str) -> Sequence[Recording]: ...


async def lookup_recording(candidate: Candidate, client: RecordingLookup) -> RecordingMatch | None:
    """Resolve by ISRC first; never bypass an ambiguous or conflicting ISRC result.

    Only an empty ISRC lookup permits a name search. Source failures are deliberately
    propagated so callers can fall back without caching a failed lookup as a miss.
    """
    if candidate.isrc:
        normalized = candidate.isrc.upper()
        if len(normalized) == 12 and normalized.isascii() and normalized.isalnum():
            recordings = await client.by_isrc(normalized)
            if recordings:
                return match_recording(candidate, recordings)
    recordings = await client.search(artist=candidate.artists[0], title=candidate.title)
    return match_recording(candidate, recordings)
