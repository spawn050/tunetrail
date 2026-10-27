"""Conservative matching of Spotify candidates to canonical recordings."""

from collections.abc import Sequence
from dataclasses import dataclass

from tunetrail.domain.models import Candidate
from tunetrail.musicbrainz.models import Recording


@dataclass(frozen=True)
class RecordingMatch:
    """A uniquely supported identity with its matching method and confidence."""

    recording: Recording
    confidence: float
    method: str


def match_recording(candidate: Candidate, recordings: Sequence[Recording]) -> RecordingMatch | None:
    """Prefer matching ISRCs; otherwise require exact normalized identity and duration.

    Conflicting ISRCs or duration evidence disqualify a recording. Equally supported
    distinct recording IDs are rejected, including conflicting ISRC lookup results.
    """
    matches: dict[str, RecordingMatch] = {}
    for recording in recordings:
        match = _match(candidate, recording)
        if match is not None:
            previous = matches.get(recording.id)
            if previous is None or previous.confidence < match.confidence:
                matches[recording.id] = match
    if not matches:
        return None
    best_confidence = max(match.confidence for match in matches.values())
    best = [match for match in matches.values() if match.confidence == best_confidence]
    return best[0] if len(best) == 1 else None


def _match(candidate: Candidate, recording: Recording) -> RecordingMatch | None:
    if (
        candidate.duration_ms
        and recording.duration_ms
        and abs(candidate.duration_ms - recording.duration_ms) > 10_000
    ):
        return None
    candidate_isrc = candidate.isrc.upper() if candidate.isrc else None
    isrcs = {isrc.upper() for isrc in recording.isrcs}
    if candidate_isrc and isrcs:
        if candidate_isrc not in isrcs:
            return None
        return RecordingMatch(recording, 1.0, "isrc")
    title = _normal(candidate.title)
    if not title or title != _normal(recording.title):
        return None
    candidate_artists = {_normal(artist) for artist in candidate.artists}
    recording_artists = {
        _normal(name) for artist in recording.artists for name in [artist.name, *artist.aliases]
    }
    if not (candidate_artists.intersection(recording_artists) - {""}):
        return None
    confidence = 0.95 if candidate.duration_ms and recording.duration_ms else 0.85
    return RecordingMatch(recording, confidence, "artist_title_duration")


def _normal(value: str) -> str:
    return " ".join("".join(char if char.isalnum() else " " for char in value.casefold()).split())
