"""Explicit preferences remain separate from legacy Spotify-derived records."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.factories import feedback
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
    legacy = feedback()
    await store.add(legacy)
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
    assert list(await store.recent()) == [legacy]


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
