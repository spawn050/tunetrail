"""Tests for minimal, owner-only playback action history."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tunetrail.domain.models import ActionHistoryEntry, ActionOutcome, PlaybackAction
from tunetrail.persistence.action_history import ActionHistoryError, FileActionHistory


def entry(*, spotify_uri: str = "spotify:track:4uLU6hMCjMI75M1A2tKUQC") -> ActionHistoryEntry:
    """Create a valid confirmed-action record."""
    return ActionHistoryEntry(
        action=PlaybackAction.PLAY_NOW,
        spotify_uris=[spotify_uri],
        device_id="device-1",
        outcome=ActionOutcome.SUCCEEDED,
        created_at=datetime.now(UTC),
    )


def test_file_history_is_bounded_private_and_newest_first(tmp_path: Path) -> None:
    """History stores only a bounded sequence in an owner-only file."""
    path = tmp_path / "action-history.json"
    history = FileActionHistory(path, maximum_entries=2)
    history.record(entry(spotify_uri="spotify:track:111"))
    history.record(entry(spotify_uri="spotify:track:222"))
    history.record(entry(spotify_uri="spotify:track:333"))

    assert [record.spotify_uris for record in history.recent()] == [
        ["spotify:track:333"],
        ["spotify:track:222"],
    ]
    assert path.stat().st_mode & 0o777 == 0o600


def test_file_history_rejects_malformed_contents(tmp_path: Path) -> None:
    """Unreadable local history does not become trusted application state."""
    path = tmp_path / "action-history.json"
    path.write_text("not json", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ActionHistoryError):
        FileActionHistory(path).recent()
