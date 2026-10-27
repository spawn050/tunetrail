"""Tests for deterministic Spotify-only personalization and queue fitting."""

from datetime import UTC, datetime

import pytest

from tests.factories import candidate, feedback, intent
from tunetrail.domain.models import FamiliarityPreference, ProfileSummary
from tunetrail.recommendation.ranking import (
    PersonalizationClarification,
    build_personalized_queue_preview,
    rank_personalized_candidates,
    validate_personalized_intent,
)


def spotify_candidate(**changes: object):  # type: ignore[no-untyped-def]
    """Build a complete Spotify candidate with transparent profile-ranking metadata."""
    values: dict[str, object] = {
        "source_id": "one",
        "title": "Track One",
        "artists": ["Known Artist"],
        "album": "Album One",
        "duration_ms": 180_000,
        "explicit": False,
        "year": 2020,
        "provenance": {"spotify_uri": "spotify:track:one"},
    }
    values.update(changes)
    return candidate(**values)


def profile() -> ProfileSummary:
    """Return a compact profile that distinguishes familiar and recent tracks."""
    return ProfileSummary(
        favorite_artists=["Known Artist"],
        favorite_track_uris=["spotify:track:one"],
        recent_track_uris=["spotify:track:recent"],
        updated_at=datetime.now(UTC),
    )


def test_ranker_applies_hard_filters_feedback_and_familiarity_precedence() -> None:
    """Explicit dislike excludes candidates while familiar profile tracks outrank unknown ones."""
    parsed = intent(familiarity=FamiliarityPreference.FAMILIAR, explicit_allowed=False)
    tracks = rank_personalized_candidates(
        [
            spotify_candidate(),
            spotify_candidate(
                source_id="two",
                title="Unknown",
                artists=["Other"],
                provenance={"spotify_uri": "spotify:track:two"},
            ),
            spotify_candidate(
                source_id="bad",
                explicit=True,
                provenance={"spotify_uri": "spotify:track:bad"},
            ),
            spotify_candidate(
                source_id="disliked",
                provenance={"spotify_uri": "spotify:track:disliked"},
            ),
        ],
        parsed,
        profile=profile(),
        feedback=[feedback(spotify_uri="spotify:track:disliked", kind="dislike")],
        generated_uris=[],
    )

    assert [track.spotify_uri for track in tracks] == ["spotify:track:one", "spotify:track:two"]
    assert tracks[0].score_breakdown["taste_affinity"] == 5.0


def test_ranker_applies_duration_diversity_and_recent_track_penalties() -> None:
    """A duration-limited preview avoids recent tracks and diversifies artists/albums."""
    parsed = intent(duration_minutes=6, queue_size=3)
    tracks = rank_personalized_candidates(
        [
            spotify_candidate(),
            spotify_candidate(
                source_id="recent",
                title="Recent",
                artists=["Known Artist"],
                album="Album One",
                duration_ms=180_000,
                provenance={"spotify_uri": "spotify:track:recent"},
            ),
            spotify_candidate(
                source_id="different",
                title="Different",
                artists=["Different Artist"],
                album="Different Album",
                duration_ms=180_000,
                provenance={"spotify_uri": "spotify:track:different"},
            ),
        ],
        parsed,
        profile=profile(),
        feedback=[],
        generated_uris=[],
    )
    preview = build_personalized_queue_preview(parsed, tracks)

    assert [track.spotify_uri for track in tracks] == [
        "spotify:track:one",
        "spotify:track:different",
    ]
    assert sum(item.track.duration_ms for item in preview.items) <= 360_000
    assert "taste affinity" in preview.items[0].explanation


def test_vague_request_requires_a_refreshed_profile_but_not_generic_search() -> None:
    """Vague mood requests cannot silently degrade to public Spotify playlist search."""
    with pytest.raises(PersonalizationClarification, match="Refresh"):
        validate_personalized_intent(intent(moods=["peaceful"]), None)
