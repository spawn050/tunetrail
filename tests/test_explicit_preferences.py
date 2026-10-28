"""Explicit preferences remain separate from legacy Spotify-derived records."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tunetrail.domain.models import (
    ChatInterpretation,
    ExplicitPreference,
    PreferencePolarity,
    PreferenceSource,
    PreferenceSubject,
)
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore
from tunetrail.recommendation.explicit_profile import ExplicitProfileService, FeedbackResponse


def preference(**updates: object) -> ExplicitPreference:
    return ExplicitPreference.model_validate(
        {
            "subject": "artist",
            "name": "An Artist",
            "polarity": "like",
            "source": "direct",
            "updated_at": datetime.now(UTC),
            **updates,
        }
    )


async def test_profile_roundtrip_update_reset_and_legacy_isolation(tmp_path: Path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "profile.sqlite3")
    with sqlite3.connect(tmp_path / "profile.sqlite3") as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_summary'"
        ).fetchone() is None
        connection.execute("CREATE TABLE feedback (spotify_uri TEXT, kind TEXT, created_at TEXT)")
        connection.execute(
            "INSERT INTO feedback (spotify_uri, kind, created_at) VALUES (?, ?, ?)",
            ("spotify:track:legacy", "like", datetime.now(UTC).isoformat()),
        )
    original = preference()
    await store.set_preference(original)
    replacement = preference(name="AN ARTIST", polarity="dislike", source="survey")
    await store.set_preference(replacement)
    assert await store.explicit_preferences() == [replacement]
    assert replacement.subject is PreferenceSubject.ARTIST
    assert replacement.polarity is PreferencePolarity.DISLIKE
    assert replacement.source is PreferenceSource.SURVEY
    await store.reset_explicit_preferences()
    assert await store.explicit_preferences() == []
    with sqlite3.connect(tmp_path / "profile.sqlite3") as connection:
        assert connection.execute("SELECT spotify_uri FROM feedback").fetchone() == (
            "spotify:track:legacy",
        )


async def test_legacy_database_migration_preserves_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE profile_summary (id INTEGER PRIMARY KEY, favorite_artists TEXT NOT NULL, "
            "favorite_track_uris TEXT NOT NULL, recent_track_uris TEXT NOT NULL, "
            "summary TEXT, updated_at TEXT NOT NULL)"
        )
        for version in range(1, 4):
            connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, "old"))
        connection.execute(
            "INSERT INTO profile_summary VALUES (1, ?, ?, ?, ?, ?)",
            ('["old"]', "[]", "[]", "legacy", "2020-01-01T00:00:00+00:00"),
        )
    store = SQLitePersonalizationStore(path)
    await store.set_preference(preference())
    await store.reset_explicit_preferences()
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT favorite_artists, summary FROM profile_summary").fetchone()
        assert row == ('["old"]', "legacy")


def test_preference_rejects_spotify_references_and_ambiguous_recording() -> None:
    with pytest.raises(ValidationError):
        preference(name="spotify:track:abc")
    with pytest.raises(ValidationError):
        preference(subject="recording", name="Song")
    assert preference(subject="recording", name="Song", artist="Artist").artist == "Artist"


async def test_chat_service_saves_only_structured_preferences(tmp_path: Path) -> None:
    class Parser:
        async def parse(self, request: str) -> ChatInterpretation:
            assert request == "I always like jazz, private extra words"
            return ChatInterpretation.model_validate(
                {"preferences": [{"subject": "genre", "name": "jazz", "polarity": "like"}]}
            )

    database = tmp_path / "profile.sqlite3"
    store = SQLitePersonalizationStore(database)
    service = ExplicitProfileService(store)
    await service.interpret_chat("I always like jazz, private extra words", Parser())
    preferences = await service.list_preferences()
    assert len(preferences) == 1 and preferences[0].source is PreferenceSource.CHAT
    assert b"private extra words" not in database.read_bytes()
    await service.reset()
    assert await service.list_preferences() == []


async def test_explicit_feedback_skips_are_not_dislikes(tmp_path: Path) -> None:
    service = ExplicitProfileService(SQLitePersonalizationStore(tmp_path / "profile.sqlite3"))
    for response in (FeedbackResponse.SKIP, FeedbackResponse.NOT_FAMILIAR):
        await service.record_feedback(
            subject=PreferenceSubject.ARTIST,
            name="Artist",
            response=response,
            source=PreferenceSource.SURVEY,
        )
    assert await service.list_preferences() == []
    await service.record_feedback(
        subject=PreferenceSubject.ARTIST,
        name="Artist",
        response=FeedbackResponse.LIKE,
        source=PreferenceSource.TILE,
    )
    await service.record_feedback(
        subject=PreferenceSubject.ARTIST,
        name="Artist",
        response=FeedbackResponse.NOT_FAMILIAR,
        source=PreferenceSource.SURVEY,
    )
    preferences = await service.list_preferences()
    assert len(preferences) == 1
    assert preferences[0].polarity is PreferencePolarity.LIKE
    assert preferences[0].source is PreferenceSource.TILE
