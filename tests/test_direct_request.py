"""Tests for deterministic Spotify-only direct-request handling."""

import pytest

from tests.factories import candidate, intent
from tunetrail.services.direct_request import (
    DirectRequestClarification,
    build_direct_queue_preview,
    rank_direct_candidates,
    spotify_search_query,
    validate_direct_intent,
)


def spotify_candidate(**changes: object):  # type: ignore[no-untyped-def]
    """Create a complete playable Spotify search result."""
    values: dict[str, object] = {
        "source_id": "track-1",
        "title": "Love Story",
        "artists": ["Taylor Swift"],
        "duration_ms": 235_000,
        "explicit": False,
        "year": 2008,
        "provenance": {"spotify_uri": "spotify:track:track1"},
    }
    values.update(changes)
    return candidate(**values)


def test_direct_request_builds_constrained_spotify_query() -> None:
    """Parsed fields become safe Spotify field filters rather than opaque LLM output."""
    parsed = intent(artists=["Taylor Swift"], year_start=2008, year_end=2012)

    assert spotify_search_query(parsed) == 'artist:"Taylor Swift" year:2008-2012'


def test_direct_ranker_filters_and_explains_matching_tracks() -> None:
    """Only complete, playable candidates meeting hard constraints enter a preview."""
    parsed = intent(artists=["Taylor Swift"], year_start=2008, year_end=2012)
    tracks = rank_direct_candidates(
        [spotify_candidate(), spotify_candidate(source_id="wrong", artists=["Other"], year=2010)],
        parsed,
    )
    preview = build_direct_queue_preview(parsed, tracks)

    assert [track.title for track in tracks] == ["Love Story"]
    assert (
        preview.items[0].explanation
        == "Spotify result matching artist: Taylor Swift, year: 2008–2012."
    )


def test_vague_or_unsupported_intents_require_clarification() -> None:
    """Phase 3 declines personalization rather than silently searching public playlists."""
    with pytest.raises(DirectRequestClarification, match="personalized mood"):
        validate_direct_intent(intent(moods=["peaceful"]))

    with pytest.raises(DirectRequestClarification, match="at most 10"):
        validate_direct_intent(intent(artists=["Radiohead"], queue_size=11))


def test_preview_requires_a_playable_match() -> None:
    """An empty retrieval result cannot reach an approval UI."""
    with pytest.raises(DirectRequestClarification, match="No playable"):
        build_direct_queue_preview(intent(artists=["Radiohead"]), [])
