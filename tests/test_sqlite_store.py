"""Tests for private SQLite personalization persistence and migrations."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.factories import feedback
from tunetrail.domain.models import (
    ActionHistoryEntry,
    ActionOutcome,
    PlaybackAction,
    PlaylistSignal,
    ProfileSummary,
)
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore


async def test_store_migrates_and_round_trips_compact_profile_data(tmp_path: Path) -> None:
    """The migration creates one private database with a replaceable profile summary."""
    database = tmp_path / "data" / "tunetrail.sqlite3"
    store = SQLitePersonalizationStore(database)
    profile = ProfileSummary(
        favorite_artists=["Artist"],
        favorite_track_uris=["spotify:track:favourite"],
        recent_track_uris=["spotify:track:recent"],
        summary="Compact profile only.",
        updated_at=datetime.now(UTC),
    )

    await store.save(profile)

    assert await store.get() == profile
    assert database.stat().st_mode & 0o777 == 0o600


async def test_store_keeps_feedback_playlist_opt_ins_and_recent_successes(tmp_path: Path) -> None:
    """Only local signals and URI-level generated-session history are persisted."""
    store = SQLitePersonalizationStore(tmp_path / "tunetrail.sqlite3")
    now = datetime.now(UTC)
    await store.add(feedback(spotify_uri="spotify:track:liked"))
    await store.replace(
        [
            PlaylistSignal(
                spotify_playlist_id="playlist-1",
                display_name="My signal",
                selected_at=now,
            )
        ]
    )
    await store.record_action(
        ActionHistoryEntry(
            action=PlaybackAction.PLAY_NOW,
            spotify_uris=["spotify:track:one", "spotify:track:two"],
            device_id="device-1",
            outcome=ActionOutcome.SUCCEEDED,
            created_at=now,
        ),
        score_json=['{"taste_affinity": 1.0}', None],
    )

    assert [item.spotify_uri for item in await store.recent()] == ["spotify:track:liked"]
    assert [item.display_name for item in await store.playlist_signals()] == ["My signal"]
    assert list(await store.recent_track_uris()) == ["spotify:track:two", "spotify:track:one"]


async def test_store_cache_expiry_and_reset_are_transactional(tmp_path: Path) -> None:
    """Expired cache rows are not trusted and reset leaves no personalization state behind."""
    store = SQLitePersonalizationStore(tmp_path / "tunetrail.sqlite3")
    now = datetime.now(UTC)
    await store.mark_cache(
        "spotify-top-tracks:short_term",
        refreshed_at=now,
        expires_at=now + timedelta(hours=1),
    )
    await store.add(feedback())
    await store.replace(
        [PlaylistSignal(spotify_playlist_id="playlist-1", display_name="Signals", selected_at=now)]
    )

    assert await store.cache_fresh("spotify-top-tracks:short_term", now=now)
    assert not await store.cache_fresh(
        "spotify-top-tracks:short_term", now=now + timedelta(hours=2)
    )
    await store.reset()

    assert await store.get() is None
    assert list(await store.recent()) == []
    assert list(await store.playlist_signals()) == []
    assert list(await store.recent_track_uris()) == []


async def test_export_contains_only_application_owned_profile_signals(tmp_path: Path) -> None:
    """Profile export omits credentials, raw requests, and upstream Spotify response payloads."""
    store = SQLitePersonalizationStore(tmp_path / "tunetrail.sqlite3")
    now = datetime.now(UTC)
    await store.save(ProfileSummary(favorite_artists=["Artist"], updated_at=now))
    await store.add(feedback())

    exported = await store.export_data()

    assert exported["profile"] is not None
    assert exported["feedback"]
    assert "credentials" not in exported
    assert "requests" not in exported
