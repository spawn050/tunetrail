"""Tests for pure, reviewable manual queue previews."""

import pytest

from tests.factories import resolved_track
from tunetrail.domain.models import PlaybackAction
from tunetrail.services.queue_preview import build_queue_preview


def test_preview_is_created_without_a_spotify_mutation() -> None:
    """A selected catalog track produces a standalone preview model."""
    preview = build_queue_preview(
        [resolved_track()],
        action=PlaybackAction.APPEND_QUEUE,
        query="track",
        explanation="Selected from Spotify track search.",
    )

    assert preview.intent.action is PlaybackAction.APPEND_QUEUE
    assert preview.items[0].track.title == "Track"


def test_preview_rejects_empty_tracks_and_unsafe_actions() -> None:
    """Only reviewable playback mutations can use the manual preview builder."""
    with pytest.raises(ValueError, match="at least one"):
        build_queue_preview(
            [],
            action=PlaybackAction.PLAY_NOW,
            query="track",
            explanation="Selected from Spotify track search.",
        )
    with pytest.raises(ValueError, match="play-now"):
        build_queue_preview(
            [resolved_track()],
            action=PlaybackAction.REPLACE_QUEUE,
            query="track",
            explanation="Selected from Spotify track search.",
        )
