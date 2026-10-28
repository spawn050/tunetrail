"""Explicit preference operations independent of playback and legacy data."""

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from tunetrail.domain.models import (
    ChatInterpretation,
    ExplicitPreference,
    PreferenceSource,
    PreferenceSubject,
)


class FeedbackResponse(StrEnum):
    """Explicit response to an independently identified suggestion."""

    LIKE = "like"
    DISLIKE = "dislike"
    SKIP = "skip"
    NOT_FAMILIAR = "not_familiar"


class PreferenceStore(Protocol):
    """Minimal independent-profile persistence interface."""

    async def set_preferences(self, preferences: Sequence[ExplicitPreference]) -> None: ...

    async def explicit_preferences(self) -> list[ExplicitPreference]: ...

    async def reset_explicit_preferences(self) -> None: ...

    async def remove_preference(self, preference: ExplicitPreference) -> None: ...


class PreferenceChatParser(Protocol):
    """Interpret chat without retaining the raw message."""

    async def parse(self, request: str) -> ChatInterpretation: ...


class ExplicitProfileService:
    """Persist validated explicit preferences independently of Spotify."""

    def __init__(self, store: PreferenceStore) -> None:
        self._store = store

    async def interpret_chat(
        self, request: str, parser: PreferenceChatParser
    ) -> ChatInterpretation:
        """Save clear preferences atomically; return the interpretation for the caller."""
        interpretation = await parser.parse(request)
        now = datetime.now(UTC)
        preferences = [
            ExplicitPreference(
                subject=item.subject,
                name=item.name,
                artist=item.artist,
                strength=item.strength,
                source=PreferenceSource.CHAT,
                updated_at=now,
            )
            for item in interpretation.preferences
        ]
        if preferences:
            await self._store.set_preferences(preferences)
        return interpretation

    async def list_preferences(self) -> list[ExplicitPreference]:
        """Inspect the independently stored profile."""
        return await self._store.explicit_preferences()

    async def set_preference(self, preference: ExplicitPreference) -> None:
        """Save a directly expressed preference, including neutral or strong sentiment."""
        if preference.source is not PreferenceSource.DIRECT:
            raise ValueError("Direct preference must have direct provenance")
        await self._store.set_preferences([preference])

    async def record_feedback(
        self,
        *,
        subject: PreferenceSubject,
        name: str,
        response: FeedbackResponse,
        source: PreferenceSource,
        artist: str | None = None,
        musicbrainz_id: str | None = None,
    ) -> None:
        """Save an explicit like/dislike, never interpreting a skip as taste."""
        response = FeedbackResponse(response)
        source = PreferenceSource(source)
        if source not in (PreferenceSource.DIRECT, PreferenceSource.TILE, PreferenceSource.SURVEY):
            raise ValueError("Feedback source must be direct, tile or survey")
        if response in (FeedbackResponse.SKIP, FeedbackResponse.NOT_FAMILIAR):
            return
        preference = ExplicitPreference(
            subject=subject,
            name=name,
            artist=artist,
            musicbrainz_id=musicbrainz_id,
            strength=1 if response is FeedbackResponse.LIKE else -1,
            source=source,
            updated_at=datetime.now(UTC),
        )
        await self._store.set_preferences([preference])

    async def reset(self) -> None:
        """Remove the independent profile while preserving legacy data."""
        await self._store.reset_explicit_preferences()

    async def remove(self, preference: ExplicitPreference) -> None:
        """Remove one preference; neutral is represented separately as strength zero."""
        await self._store.remove_preference(preference)
