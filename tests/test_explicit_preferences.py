"""Explicit preferences remain separate from legacy Spotify-derived records."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.factories import feedback
from tunetrail.domain.models import (
    ExplicitPreference,
    PreferencePolarity,
    PreferenceSource,
    PreferenceSubject,
)
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore


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
