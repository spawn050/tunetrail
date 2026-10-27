"""Validated, provider-neutral domain models."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class PlaybackAction(StrEnum):
    """The player mutation requested by a listening intent."""

    PLAY_NOW = "play_now"
    APPEND_QUEUE = "append_queue"
    REPLACE_QUEUE = "replace_queue"
    REFINE = "refine"


class FamiliarityPreference(StrEnum):
    """How much known music a generated queue should contain."""

    FAMILIAR = "familiar"
    MIXED = "mixed"
    DISCOVERY = "discovery"


class CandidateSource(StrEnum):
    """A service or local source that suggested a candidate."""

    SPOTIFY = "spotify"
    LASTFM = "lastfm"
    MUSICBRAINZ = "musicbrainz"
    RECCOBEATS = "reccobeats"
    LOCAL = "local"


class CatalogItemKind(StrEnum):
    """Spotify catalog resource types that can provide queue tracks."""

    TRACK = "track"
    ARTIST = "artist"
    ALBUM = "album"
    PLAYLIST = "playlist"


class FeedbackKind(StrEnum):
    """Explicit feedback a listener can provide for a track or queue."""

    LIKE = "like"
    DISLIKE = "dislike"
    MORE_LIKE_THIS = "more_like_this"
    LESS_LIKE_THIS = "less_like_this"
    TOO_FAMILIAR = "too_familiar"
    TOO_DISTRACTING = "too_distracting"


class ActionOutcome(StrEnum):
    """The local result of a confirmed Spotify player action."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


NonEmptyText = Annotated[str, Field(min_length=1)]


class ListeningIntent(BaseModel):
    """A validated interpretation of a listener's request."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: PlaybackAction = PlaybackAction.PLAY_NOW
    artists: list[NonEmptyText] = Field(default_factory=list)
    track_terms: list[NonEmptyText] = Field(default_factory=list)
    year_start: int | None = Field(default=None, ge=1860, le=2100)
    year_end: int | None = Field(default=None, ge=1860, le=2100)
    moods: list[NonEmptyText] = Field(default_factory=list)
    activities: list[NonEmptyText] = Field(default_factory=list)
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    queue_size: int = Field(default=20, ge=1, le=100)
    familiarity: FamiliarityPreference = FamiliarityPreference.MIXED
    explicit_allowed: bool | None = None
    clarification_needed: bool = False

    @field_validator("artists", "track_terms", "moods", "activities")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        """Remove duplicate terms while retaining their supplied order."""
        normalized = [value.strip() for value in values]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_year_range(self) -> "ListeningIntent":
        """Reject date ranges whose end precedes their beginning."""
        if (
            self.year_start is not None
            and self.year_end is not None
            and self.year_end < self.year_start
        ):
            raise ValueError("year_end must not be earlier than year_start")
        return self


class Candidate(BaseModel):
    """A possible track before Spotify playback resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source: CandidateSource
    source_id: NonEmptyText
    title: NonEmptyText
    artists: list[NonEmptyText] = Field(min_length=1)
    album: str | None = None
    duration_ms: int | None = Field(default=None, gt=0)
    isrc: str | None = None
    year: int | None = Field(default=None, ge=1860, le=2100)
    explicit: bool | None = None
    source_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance: dict[str, str] = Field(default_factory=dict)

    @field_validator("artists")
    @classmethod
    def unique_artists(cls, values: list[str]) -> list[str]:
        """Normalize duplicate artist names without changing their order."""
        return list(dict.fromkeys(value.strip() for value in values))


class ResolvedTrack(BaseModel):
    """A candidate mapped to a playable Spotify track with match evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    spotify_id: NonEmptyText
    spotify_uri: str = Field(pattern=r"^spotify:track:[A-Za-z0-9]+$")
    title: NonEmptyText
    artists: list[NonEmptyText] = Field(min_length=1)
    album: str | None = None
    duration_ms: int = Field(gt=0)
    explicit: bool
    match_confidence: float = Field(ge=0.0, le=1.0)
    candidates: list[Candidate] = Field(min_length=1)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class CatalogItem(BaseModel):
    """A selectable Spotify catalog item returned by an explicit search."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: CatalogItemKind
    spotify_id: NonEmptyText
    spotify_uri: NonEmptyText
    title: NonEmptyText
    subtitle: str | None = None
    track: ResolvedTrack | None = None

    @model_validator(mode="after")
    def validate_track_result(self) -> "CatalogItem":
        """Require playable track metadata for track results only."""
        if self.kind is CatalogItemKind.TRACK and self.track is None:
            raise ValueError("track catalog results must include a resolved track")
        if self.kind is not CatalogItemKind.TRACK and self.track is not None:
            raise ValueError("non-track catalog results cannot include a resolved track")
        return self


class Device(BaseModel):
    """A Spotify playback device."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str | None = None
    name: NonEmptyText
    kind: NonEmptyText
    is_active: bool = False
    is_restricted: bool = False
    volume_percent: int | None = Field(default=None, ge=0, le=100)


class PlaybackState(BaseModel):
    """Current playback state reported by Spotify."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device: Device | None = None
    track: ResolvedTrack | None = None
    is_playing: bool = False
    progress_ms: int | None = Field(default=None, ge=0)
    fetched_at: AwareDatetime


class QueueItem(BaseModel):
    """A track proposed for a queue, including its user-facing rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    track: ResolvedTrack
    explanation: NonEmptyText


class QueuePreview(BaseModel):
    """A reviewable queue that has not yet changed Spotify playback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: ListeningIntent
    items: list[QueueItem] = Field(min_length=1)
    created_at: AwareDatetime


class CurrentQueue(BaseModel):
    """Spotify's visible current item and upcoming playable tracks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current: ResolvedTrack | None = None
    upcoming: list[ResolvedTrack] = Field(default_factory=list[ResolvedTrack])


class ActionHistoryEntry(BaseModel):
    """Minimal local record of a confirmed playback action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: PlaybackAction
    spotify_uris: list[str] = Field(min_length=1)
    device_id: str | None = None
    outcome: ActionOutcome
    created_at: AwareDatetime

    @field_validator("spotify_uris")
    @classmethod
    def validate_track_uris(cls, values: list[str]) -> list[str]:
        """Retain unique playable track URIs only."""
        unique = list(dict.fromkeys(values))
        if any(not uri.startswith("spotify:track:") for uri in unique):
            raise ValueError("action history can contain only Spotify track URIs")
        return unique


class Feedback(BaseModel):
    """An explicit local listening preference."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: FeedbackKind
    spotify_uri: str = Field(pattern=r"^spotify:track:[A-Za-z0-9]+$")
    created_at: AwareDatetime
    note: str | None = Field(default=None, max_length=500)


class ProfileSummary(BaseModel):
    """A compact, local-only summary of taste signals."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    favorite_artists: list[NonEmptyText] = Field(default_factory=list)
    favorite_track_uris: list[str] = Field(default_factory=list)
    recent_track_uris: list[str] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=2_000)
    updated_at: AwareDatetime


Timestamp = datetime
