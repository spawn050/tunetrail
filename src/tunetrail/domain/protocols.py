"""Provider-neutral interfaces for infrastructure and application services."""

from collections.abc import Sequence
from typing import Protocol

from tunetrail.domain.models import (
    ActionHistoryEntry,
    Candidate,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    Feedback,
    ListeningIntent,
    PlaybackState,
    ProfileSummary,
    ResolvedTrack,
)
from tunetrail.graph.models import ApprovalDecision, RequestGraphResult


class CredentialStore(Protocol):
    """Persist authentication material outside application logs."""

    async def load(self) -> dict[str, str] | None: ...

    async def save(self, credentials: dict[str, str]) -> None: ...

    async def clear(self) -> None: ...


class SpotifyGateway(Protocol):
    """The subset of Spotify operations required by TuneTrail."""

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


class RequestGraph(Protocol):
    """Turn a natural-language request into an approval-gated result."""

    async def start(self, request: str, *, device_id: str | None = None) -> RequestGraphResult: ...

    async def resume(self, decision: ApprovalDecision) -> RequestGraphResult: ...


class LLMFactory(Protocol):
    """Create an LLM-backed intent parser from configured providers."""

    def create_intent_parser(self) -> IntentParser: ...


class ProfileRepository(Protocol):
    """Store and retrieve compact local taste summaries."""

    async def get(self) -> ProfileSummary | None: ...

    async def save(self, profile: ProfileSummary) -> None: ...


class FeedbackRepository(Protocol):
    """Record user feedback without retaining credentials."""

    async def add(self, feedback: Feedback) -> None: ...

    async def recent(self, *, limit: int = 100) -> Sequence[Feedback]: ...


class ActionHistoryRepository(Protocol):
    """Persist minimal confirmed playback-action records locally."""

    def record(self, entry: ActionHistoryEntry) -> None: ...

    def recent(self, *, limit: int = 20) -> Sequence[ActionHistoryEntry]: ...


class CandidateSourceProvider(Protocol):
    """Retrieve non-playable track candidates for an intent."""

    async def find(
        self, intent: ListeningIntent, profile: ProfileSummary | None
    ) -> Sequence[Candidate]: ...


class TrackResolver(Protocol):
    """Resolve candidates to playable Spotify tracks."""

    async def resolve(self, candidate: Candidate) -> ResolvedTrack | None: ...


class QueueRanker(Protocol):
    """Rank resolved tracks according to transparent local rules."""

    def rank(
        self,
        tracks: Sequence[ResolvedTrack],
        intent: ListeningIntent,
        profile: ProfileSummary | None,
    ) -> Sequence[ResolvedTrack]: ...
