"""Tests for validated domain models."""

import pytest
from pydantic import ValidationError

from tests.factories import candidate, intent, resolved_track


def test_intent_normalizes_duplicate_terms() -> None:
    """Repeated request terms do not create repeated constraints."""
    parsed = intent(artists=["  Artist  ", "Artist"], moods=["calm", "calm"])

    assert parsed.artists == ["Artist"]
    assert parsed.moods == ["calm"]


def test_intent_rejects_reverse_year_range() -> None:
    """The end year cannot precede the start year."""
    with pytest.raises(ValidationError, match="year_end"):
        intent(year_start=2020, year_end=2019)


def test_candidate_requires_artist() -> None:
    """Candidates must preserve enough metadata for later resolution."""
    with pytest.raises(ValidationError):
        candidate(artists=[])


def test_resolved_track_requires_a_spotify_track_uri() -> None:
    """Only playable track URIs enter a queue preview."""
    with pytest.raises(ValidationError):
        resolved_track(spotify_uri="spotify:album:4uLU6hMCjMI75M1A2tKUQC")
