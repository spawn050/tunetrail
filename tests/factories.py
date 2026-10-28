"""Factories for valid domain objects used by tests."""

from tunetrail.domain.models import (
    Candidate,
    CandidateSource,
    ListeningIntent,
    ResolvedTrack,
)


def candidate(**changes: object) -> Candidate:
    """Build a valid candidate with optional field overrides."""
    values: dict[str, object] = {
        "source": CandidateSource.SPOTIFY,
        "source_id": "candidate-1",
        "title": "Track",
        "artists": ["Artist"],
    }
    values.update(changes)
    return Candidate.model_validate(values)


def resolved_track(**changes: object) -> ResolvedTrack:
    """Build a valid resolved track with optional field overrides."""
    values: dict[str, object] = {
        "spotify_id": "4uLU6hMCjMI75M1A2tKUQC",
        "spotify_uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        "title": "Track",
        "artists": ["Artist"],
        "duration_ms": 180_000,
        "explicit": False,
        "match_confidence": 0.99,
        "candidates": [candidate()],
    }
    values.update(changes)
    return ResolvedTrack.model_validate(values)


def intent(**changes: object) -> ListeningIntent:
    """Build a valid listening intent with optional field overrides."""
    return ListeningIntent.model_validate(changes)
