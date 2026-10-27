"""Build reviewable queue previews without any Spotify mutation."""

from collections.abc import Sequence
from datetime import UTC, datetime

from tunetrail.domain.models import (
    ListeningIntent,
    PlaybackAction,
    QueueItem,
    QueuePreview,
    ResolvedTrack,
)


def build_queue_preview(
    tracks: Sequence[ResolvedTrack],
    *,
    action: PlaybackAction,
    query: str,
    explanation: str,
) -> QueuePreview:
    """Create a manual-search preview; callers must confirm it separately."""
    if action not in {PlaybackAction.PLAY_NOW, PlaybackAction.APPEND_QUEUE}:
        raise ValueError("manual queue previews support play-now or append actions only")
    if not tracks:
        raise ValueError("queue previews require at least one playable track")
    return QueuePreview(
        intent=ListeningIntent(action=action, track_terms=[query] if query.strip() else []),
        items=[QueueItem(track=track, explanation=explanation) for track in tracks],
        created_at=datetime.now(UTC),
    )
