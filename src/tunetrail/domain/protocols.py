"""Provider-neutral interfaces for retained application services."""

from collections.abc import Sequence
from typing import Protocol

from tunetrail.domain.models import (
    Candidate,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    ListeningIntent,
    PlaybackState,
    ResolvedTrack,
)


class CredentialStore(Protocol):
    """Persist authentication material outside application logs."""

    async def load(self) -> dict[str, str] | None: ...
    async def save(self, credentials: dict[str, str]) -> None: ...
    async def clear(self) -> None: ...


class SpotifyGateway(Protocol):
    """Spotify capabilities retained for future resolution and playback."""

    async def current_playback(self) -> PlaybackState | None: ...
    async def devices(self) -> Sequence[Device]: ...
    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]: ...
    async def search_catalog(
        self, query: str, *, kind: CatalogItemKind, limit: int = 10
    ) -> Sequence[CatalogItem]: ...
    async def tracks_for_catalog_item(
        self, item: CatalogItem, *, limit: int = 10
    ) -> Sequence[ResolvedTrack]: ...
    async def current_queue(self) -> CurrentQueue | None: ...
    async def add_to_queue(self, spotify_uri: str, *, device_id: str | None = None) -> None: ...
    async def start_playback(
        self, spotify_uris: Sequence[str], *, device_id: str | None = None
    ) -> None: ...


class IntentParser(Protocol):
    """Interpret free text as a validated listening intent."""

    async def parse(self, request: str) -> ListeningIntent: ...
