"""Provider-neutral interfaces for infrastructure and application services."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol

from tunetrail.domain.models import (
    ActionHistoryEntry,
    Candidate,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    Feedback,
    LastfmProfile,
    ListeningIntent,
    PlaybackState,
    PlaylistSignal,
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

    async def top_tracks(
        self, time_range: Literal["short_term", "medium_term", "long_term"], *, limit: int = 50
    ) -> Sequence[ResolvedTrack]: ...

    async def top_artists(
        self, time_range: Literal["short_term", "medium_term", "long_term"], *, limit: int = 50
    ) -> Sequence[str]: ...

    async def recently_played_tracks(self, *, limit: int = 50) -> Sequence[ResolvedTrack]: ...

    async def user_playlists(self, *, limit: int = 50) -> Sequence[CatalogItem]: ...

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


class LastfmGateway(Protocol):
    """The bounded public Last.fm reads used for optional discovery enrichment."""

    async def user_top_artists(self, *, limit: int = 20) -> Sequence[str]: ...

    async def user_top_tracks(self, *, limit: int = 20) -> Sequence[Candidate]: ...

    async def user_recent_tracks(self, *, limit: int = 20) -> Sequence[Candidate]: ...

    async def similar_tracks(
        self, *, artist: str, title: str, limit: int = 10
    ) -> Sequence[Candidate]: ...

    async def similar_artists(self, artist: str, *, limit: int = 5) -> Sequence[str]: ...

    async def artist_top_tracks(self, artist: str, *, limit: int = 3) -> Sequence[Candidate]: ...

    async def track_tags(self, *, artist: str, title: str, limit: int = 5) -> Sequence[str]: ...

    async def artist_tags(self, artist: str, *, limit: int = 5) -> Sequence[str]: ...


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


class PlaylistSignalRepository(Protocol):
    """Persist the user's explicit playlist-ingestion choices only."""

    async def playlist_signals(self) -> Sequence[PlaylistSignal]: ...

    async def replace(self, signals: Sequence[PlaylistSignal]) -> None: ...


class SessionRepository(Protocol):
    """Store generated-session metadata for local repetition avoidance."""

    async def record_action(
        self, entry: ActionHistoryEntry, *, score_json: Sequence[str | None]
    ) -> None: ...

    async def recent_track_uris(self, *, limit: int = 100) -> Sequence[str]: ...


class PersonalizationStore(
    ProfileRepository, FeedbackRepository, PlaylistSignalRepository, SessionRepository, Protocol
):
    """The local persistence surface required by Spotify-only personalization."""

    async def save_profile_refresh(
        self,
        profile: ProfileSummary,
        signals: Sequence[PlaylistSignal],
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def reset(self) -> None: ...

    async def export_data(self) -> dict[str, object]: ...

    async def lastfm_profile(self, cache_key: str, *, now: datetime) -> LastfmProfile | None: ...

    async def save_lastfm_profile(
        self,
        cache_key: str,
        profile: LastfmProfile,
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def lastfm_candidates(
        self, cache_key: str, *, now: datetime
    ) -> Sequence[Candidate] | None: ...

    async def save_lastfm_candidates(
        self,
        cache_key: str,
        candidates: Sequence[Candidate],
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None: ...


class ProfileRefresher(Protocol):
    """Refresh a compact local profile from allowed Spotify taste signals."""

    async def refresh(self) -> ProfileSummary: ...


class ActionHistoryRepository(Protocol):
    """Persist minimal confirmed playback-action records locally."""

    def record(self, entry: ActionHistoryEntry) -> None: ...

    def recent(self, *, limit: int = 20) -> Sequence[ActionHistoryEntry]: ...


class CandidateSourceProvider(Protocol):
    """Retrieve non-playable track candidates for an intent."""

    async def find(
        self, intent: ListeningIntent, profile: ProfileSummary | None
    ) -> Sequence[Candidate]: ...


class CandidateMetadataProvider(Protocol):
    """Optionally annotate Spotify-resolved candidates without changing playback state."""

    async def enrich(self, candidates: Sequence[Candidate]) -> Sequence[Candidate]: ...


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
