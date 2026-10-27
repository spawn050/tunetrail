"""Explicit preference operations independent of playback and legacy data."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from tunetrail.domain.models import ChatInterpretation, ExplicitPreference, PreferenceSource


class PreferenceStore(Protocol):
    """Minimal independent-profile persistence interface."""

    async def set_preferences(self, preferences: Sequence[ExplicitPreference]) -> None: ...

    async def explicit_preferences(self) -> list[ExplicitPreference]: ...

    async def reset_explicit_preferences(self) -> None: ...


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
                polarity=item.polarity,
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

    async def reset(self) -> None:
        """Remove the independent profile while preserving legacy data."""
        await self._store.reset_explicit_preferences()
