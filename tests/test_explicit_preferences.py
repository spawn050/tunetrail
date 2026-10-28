"""Explicit preferences remain separate from legacy Spotify-derived records."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from tunetrail.domain.models import (
    ChatInterpretation,
    ExplicitPreference,
    PreferenceSource,
    PreferenceSubject,
)
from tunetrail.llm.intent_parser import IntentParseError, StructuredPreferenceParser
from tunetrail.persistence.sqlite_store import (
    IncompatibleProfileError,
    PreferenceIdentityConflict,
    SQLitePersonalizationStore,
)
from tunetrail.recommendation.explicit_profile import ExplicitProfileService, FeedbackResponse


def preference(**updates: object) -> ExplicitPreference:
    return ExplicitPreference.model_validate(
        {
            "subject": "artist",
            "name": "An Artist",
            "strength": 1,
            "source": "direct",
            "updated_at": datetime.now(UTC),
            **updates,
        }
    )


async def test_profile_roundtrip_update_reset_and_legacy_isolation(tmp_path: Path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "profile.sqlite3")
    with sqlite3.connect(tmp_path / "profile.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_summary'"
            ).fetchone()
            is None
        )
        connection.execute("CREATE TABLE feedback (spotify_uri TEXT, kind TEXT, created_at TEXT)")
        connection.execute(
            "INSERT INTO feedback (spotify_uri, kind, created_at) VALUES (?, ?, ?)",
            ("spotify:track:legacy", "like", datetime.now(UTC).isoformat()),
        )
    original = preference()
    await store.set_preference(original)
    replacement = preference(name="AN ARTIST", strength=-1, source="survey")
    await store.set_preference(replacement)
    assert await store.explicit_preferences() == [replacement]
    assert replacement.subject is PreferenceSubject.ARTIST
    assert replacement.strength == -1
    assert replacement.source is PreferenceSource.SURVEY
    await store.reset_explicit_preferences()
    assert await store.explicit_preferences() == []
    with sqlite3.connect(tmp_path / "profile.sqlite3") as connection:
        assert connection.execute("SELECT spotify_uri FROM feedback").fetchone() == (
            "spotify:track:legacy",
        )


async def test_legacy_database_is_not_modified(tmp_path: Path) -> None:
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
    before = path.read_bytes()
    with pytest.raises(IncompatibleProfileError):
        SQLitePersonalizationStore(path)
    assert path.read_bytes() == before
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
                {"preferences": [{"subject": "genre", "name": "jazz", "strength": 1}]}
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
    assert preferences[0].strength == 1
    assert preferences[0].source is PreferenceSource.TILE


async def test_identity_attachment_conflicts_and_atomicity(tmp_path: Path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "profile.sqlite3")
    mbid = "12345678-1234-1234-1234-123456789abc"
    await store.set_preference(preference(strength=0))
    await store.set_preference(preference(name="AN ARTIST", musicbrainz_id=mbid))
    await store.set_preference(preference(name="An Artist", strength=-2))
    saved = await store.explicit_preferences()
    assert len(saved) == 1 and saved[0].musicbrainz_id == mbid and saved[0].strength == -2
    with pytest.raises(PreferenceIdentityConflict):
        await store.set_preferences(
            [
                preference(name="Other"),
                preference(musicbrainz_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            ]
        )
    assert await store.explicit_preferences() == saved
    with pytest.raises(PreferenceIdentityConflict):
        await store.set_preference(preference(name="Different", musicbrainz_id=mbid))
    await store.remove_preference(saved[0])
    assert await store.explicit_preferences() == []


@pytest.mark.parametrize("strength", [-2, -1, 0, 1, 2])
async def test_strength_roundtrip(tmp_path: Path, strength: int) -> None:
    store = SQLitePersonalizationStore(tmp_path / "profile.sqlite3")
    await store.set_preference(preference(strength=strength))
    assert (await store.explicit_preferences())[0].strength == strength
    assert (await SQLitePersonalizationStore(tmp_path / "profile.sqlite3").explicit_preferences())[
        0
    ].strength == strength


@pytest.mark.parametrize("strength", [-3, 3, True, "1", 1.0])
def test_invalid_strength_is_rejected(strength: object) -> None:
    with pytest.raises(ValidationError):
        preference(strength=strength)


async def test_chat_temporary_and_clarification_do_not_save(tmp_path: Path) -> None:
    class Parser:
        def __init__(self, result: ChatInterpretation) -> None:
            self.result = result

        async def parse(self, request: str) -> ChatInterpretation:
            return self.result

    service = ExplicitProfileService(SQLitePersonalizationStore(tmp_path / "profile.sqlite3"))
    for result in (
        ChatInterpretation.model_validate({"intent": {}}),
        ChatInterpretation.model_validate({"clarification": "Always?"}),
    ):
        assert await service.interpret_chat("private text", Parser(result)) == result
    assert await service.list_preferences() == []


async def test_structured_chat_to_profile_neutral_and_temporary_intent(tmp_path: Path) -> None:
    class Model:
        async def ainvoke(self, input: object) -> object:
            return {
                "intent": {"moods": ["calm"]},
                "preferences": [{"subject": "genre", "name": "jazz", "strength": 0}],
            }

    path = tmp_path / "profile.sqlite3"
    service = ExplicitProfileService(SQLitePersonalizationStore(path))
    interpretation = await service.interpret_chat(
        "private words: mixed feelings about jazz; play something calm",
        StructuredPreferenceParser(Model()),
    )
    assert interpretation.intent is not None and interpretation.intent.moods == ["calm"]
    saved = await service.list_preferences()
    assert len(saved) == 1 and saved[0].strength == 0 and saved[0].source is PreferenceSource.CHAT
    assert b"private words" not in path.read_bytes()
    await service.remove(saved[0])
    assert await service.list_preferences() == []


async def test_sql_constraints_and_direct_strength(tmp_path: Path) -> None:
    path = tmp_path / "profile.sqlite3"
    service = ExplicitProfileService(SQLitePersonalizationStore(path))
    await service.set_preference(preference(strength=2))
    with pytest.raises(ValueError, match="provenance"):
        await service.set_preference(preference(source="survey"))
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE explicit_preference SET strength=3")


@pytest.mark.parametrize("response", ["like", "dislike", "skip", "not_familiar"])
async def test_feedback_string_values_are_validated(tmp_path: Path, response: str) -> None:
    service = ExplicitProfileService(SQLitePersonalizationStore(tmp_path / "profile.sqlite3"))
    await service.record_feedback(
        subject=PreferenceSubject.ARTIST,
        name="Artist",
        response=cast(FeedbackResponse, response),
        source=PreferenceSource.SURVEY,
    )
    saved = await service.list_preferences()
    if response in ("skip", "not_familiar"):
        assert not saved
    else:
        assert saved[0].strength == (1 if response == "like" else -1)
        assert saved[0].source is PreferenceSource.SURVEY


async def test_unknown_feedback_never_overwrites_preference(tmp_path: Path) -> None:
    service = ExplicitProfileService(SQLitePersonalizationStore(tmp_path / "profile.sqlite3"))
    await service.set_preference(preference(strength=0))
    before = await service.list_preferences()
    with pytest.raises(ValueError):
        await service.record_feedback(
            subject=PreferenceSubject.ARTIST,
            name="An Artist",
            response=cast(FeedbackResponse, "approve"),
            source=PreferenceSource.TILE,
        )
    assert await service.list_preferences() == before


def test_old_json_profile_is_rejected_without_changes(tmp_path: Path) -> None:
    path = tmp_path / "profile.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE explicit_preference (identity_key TEXT, value_json TEXT)")
        connection.execute("INSERT INTO explicit_preference VALUES (?, ?)", ("old", '{"x":1}'))
    before = path.read_bytes()
    with pytest.raises(IncompatibleProfileError):
        SQLitePersonalizationStore(path)
    assert path.read_bytes() == before


async def test_reset_only_clears_explicit_preferences(tmp_path: Path) -> None:
    path = tmp_path / "profile.sqlite3"
    service = ExplicitProfileService(SQLitePersonalizationStore(path))
    files = [
        tmp_path / name for name in ("auth.json", "profile-export.json", "action-history.json")
    ]
    for file in files:
        file.write_text("keep", encoding="utf-8")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_cache (payload TEXT)")
        connection.execute("INSERT INTO legacy_cache VALUES ('keep')")
    await service.set_preference(preference())
    await service.reset()
    assert await service.list_preferences() == []
    assert all(file.read_text(encoding="utf-8") == "keep" for file in files)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT payload FROM legacy_cache").fetchall() == [("keep",)]


async def test_invalid_model_output_cannot_modify_profile(tmp_path: Path) -> None:
    class Model:
        async def ainvoke(self, input: object) -> object:
            return {
                "clarification": "Always?",
                "preferences": [{"subject": "genre", "name": "jazz", "strength": 1}],
            }

    service = ExplicitProfileService(SQLitePersonalizationStore(tmp_path / "profile.sqlite3"))
    await service.set_preference(preference())
    before = await service.list_preferences()
    with pytest.raises(IntentParseError):
        await service.interpret_chat("ambiguous private text", StructuredPreferenceParser(Model()))
    assert await service.list_preferences() == before


async def test_profile_flow_does_not_call_http_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected_request(*args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("The explicit profile flow must not call an HTTP provider")

    class LocalModel:
        async def ainvoke(self, input: object) -> object:
            return {"preferences": [{"subject": "genre", "name": "jazz", "strength": 0}]}

    monkeypatch.setattr(httpx.AsyncClient, "request", unexpected_request)
    service = ExplicitProfileService(SQLitePersonalizationStore(tmp_path / "profile.sqlite3"))
    await service.interpret_chat(
        "I have mixed feelings about jazz", StructuredPreferenceParser(LocalModel())
    )
    await service.record_feedback(
        subject=PreferenceSubject.ARTIST,
        name="Artist",
        response=FeedbackResponse.LIKE,
        source=PreferenceSource.TILE,
    )
    assert len(await service.list_preferences()) == 2
    await service.reset()
    assert not await service.list_preferences()


async def test_profile_limit_and_batch_rollback(tmp_path: Path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "profile.sqlite3")
    await store.set_preferences([preference(name=f"Artist {i}") for i in range(999)])
    with pytest.raises(ValueError, match="limit"):
        await store.set_preferences([preference(name="Last"), preference(name="Too many")])
    saved = await store.explicit_preferences()
    assert len(saved) == 999 and all(item.name != "Last" for item in saved)
