"""Safe mappings from Spotify Web API payloads to TuneTrail domain models."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from tunetrail.domain.models import (
    Candidate,
    CandidateSource,
    CatalogItem,
    CatalogItemKind,
    CurrentQueue,
    Device,
    PlaybackState,
    ResolvedTrack,
)


class SpotifyPayloadError(ValueError):
    """Raised when Spotify returns data that cannot become a safe domain model."""


class SpotifyUser(BaseModel):
    """The small portion of the current Spotify profile used in Phase 1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    display_name: str | None = None


def device_from_payload(payload: Mapping[str, object]) -> Device:
    """Map a Spotify device object to a provider-neutral device."""
    name = _required_text(payload, "name")
    kind = _required_text(payload, "type")
    identifier = payload.get("id")
    volume = _mapping(payload.get("volume_percent"))
    volume_percent = payload.get("volume_percent")
    if volume is not None:
        volume_percent = volume.get("value")
    return Device(
        id=identifier if isinstance(identifier, str) else None,
        name=name,
        kind=kind,
        is_active=payload.get("is_active") is True,
        is_restricted=payload.get("is_restricted") is True,
        volume_percent=volume_percent if isinstance(volume_percent, int) else None,
    )


def candidate_from_track(payload: Mapping[str, object]) -> Candidate:
    """Map a Spotify simplified track object to a candidate."""
    identifier = _required_text(payload, "id")
    artists = _artist_names(payload)
    album = _mapping(payload.get("album"))
    external_ids = _mapping(payload.get("external_ids"))
    release_date = album.get("release_date") if album is not None else None
    explicit = payload.get("explicit")
    return Candidate(
        source=CandidateSource.SPOTIFY,
        source_id=identifier,
        title=_required_text(payload, "name"),
        artists=artists,
        album=_optional_text(album, "name"),
        duration_ms=_optional_int(payload, "duration_ms"),
        isrc=_optional_text(external_ids, "isrc"),
        year=_release_year(release_date),
        explicit=explicit if isinstance(explicit, bool) else None,
        provenance={"spotify_uri": _required_text(payload, "uri")},
    )


def resolved_track_from_payload(payload: Mapping[str, object]) -> ResolvedTrack:
    """Map a full Spotify track object to a resolved, playable track."""
    candidate = candidate_from_track(payload)
    uri = _required_text(payload, "uri")
    if not uri.startswith("spotify:track:"):
        raise SpotifyPayloadError("Spotify item was not a playable track")
    duration_ms = _optional_int(payload, "duration_ms")
    explicit = payload.get("explicit")
    if duration_ms is None or not isinstance(explicit, bool):
        raise SpotifyPayloadError("Spotify track lacked required playback metadata")
    return ResolvedTrack(
        spotify_id=candidate.source_id,
        spotify_uri=uri,
        title=candidate.title,
        artists=candidate.artists,
        album=candidate.album,
        duration_ms=duration_ms,
        explicit=explicit,
        match_confidence=1.0,
        candidates=[candidate],
    )


def catalog_item_from_track(payload: Mapping[str, object]) -> CatalogItem:
    """Map a Spotify track search result to a selectable catalog item."""
    track = resolved_track_from_payload(payload)
    return CatalogItem(
        kind=CatalogItemKind.TRACK,
        spotify_id=track.spotify_id,
        spotify_uri=track.spotify_uri,
        title=track.title,
        subtitle=", ".join(track.artists),
        track=track,
    )


def catalog_item_from_artist(payload: Mapping[str, object]) -> CatalogItem:
    """Map a Spotify artist search result to a selectable catalog context."""
    return CatalogItem(
        kind=CatalogItemKind.ARTIST,
        spotify_id=_required_text(payload, "id"),
        spotify_uri=_required_text(payload, "uri"),
        title=_required_text(payload, "name"),
        subtitle=_optional_text(payload, "type"),
    )


def catalog_item_from_album(payload: Mapping[str, object]) -> CatalogItem:
    """Map a Spotify album search result to a selectable catalog context."""
    return CatalogItem(
        kind=CatalogItemKind.ALBUM,
        spotify_id=_required_text(payload, "id"),
        spotify_uri=_required_text(payload, "uri"),
        title=_required_text(payload, "name"),
        subtitle=", ".join(_artist_names(payload)),
    )


def catalog_item_from_playlist(payload: Mapping[str, object]) -> CatalogItem:
    """Map a Spotify playlist search result to a selectable catalog context."""
    owner = _mapping(payload.get("owner"))
    return CatalogItem(
        kind=CatalogItemKind.PLAYLIST,
        spotify_id=_required_text(payload, "id"),
        spotify_uri=_required_text(payload, "uri"),
        title=_required_text(payload, "name"),
        subtitle=_optional_text(owner, "display_name"),
    )


def current_queue_from_payload(payload: Mapping[str, object]) -> CurrentQueue:
    """Map Spotify's queue response, excluding non-track queue entries."""
    current_payload = _mapping(payload.get("currently_playing"))
    queue_payload = payload.get("queue")
    if not isinstance(queue_payload, list):
        raise SpotifyPayloadError("Spotify queue response was malformed")
    return CurrentQueue(
        current=_resolved_track_or_none(current_payload),
        upcoming=resolved_tracks_from_values(cast(list[object], queue_payload)),
    )


def resolved_tracks_from_items(payload: Mapping[str, object]) -> list[ResolvedTrack]:
    """Map an items response, unwrapping playlist entries when necessary."""
    items = payload.get("items")
    if not isinstance(items, list):
        raise SpotifyPayloadError("Spotify items response was malformed")
    tracks: list[ResolvedTrack] = []
    for item in cast(list[object], items):
        item_payload = _mapping(item)
        if item_payload is None:
            continue
        track_payload = _mapping(item_payload.get("item")) or item_payload
        track = _resolved_track_or_none(track_payload)
        if track is not None:
            tracks.append(track)
    return tracks


def resolved_tracks_from_values(values: list[object]) -> list[ResolvedTrack]:
    """Map a list of Spotify track values, omitting unsupported entries."""
    tracks: list[ResolvedTrack] = []
    for value in values:
        track = _resolved_track_or_none(_mapping(value))
        if track is not None:
            tracks.append(track)
    return tracks


def playback_from_payload(payload: Mapping[str, object]) -> PlaybackState:
    """Map Spotify's current-playback response to a provider-neutral state."""
    device_payload = _mapping(payload.get("device"))
    item_payload = _mapping(payload.get("item"))
    track: ResolvedTrack | None = None
    if item_payload is not None and item_payload.get("type") == "track":
        track = resolved_track_from_payload(item_payload)
    return PlaybackState(
        device=device_from_payload(device_payload) if device_payload is not None else None,
        track=track,
        is_playing=payload.get("is_playing") is True,
        progress_ms=_optional_int(payload, "progress_ms"),
        fetched_at=datetime.now(UTC),
    )


def _artist_names(payload: Mapping[str, object]) -> list[str]:
    artists = payload.get("artists")
    if not isinstance(artists, list):
        raise SpotifyPayloadError("Spotify track lacked artist metadata")
    artist_objects = cast(list[object], artists)
    names = [_required_text(_mapping_or_error(item), "name") for item in artist_objects]
    if not names:
        raise SpotifyPayloadError("Spotify track had no artists")
    return names


def _resolved_track_or_none(payload: Mapping[str, object] | None) -> ResolvedTrack | None:
    """Convert a track payload when it is a supported playable track."""
    if payload is None or payload.get("type") not in {None, "track"}:
        return None
    try:
        return resolved_track_from_payload(payload)
    except SpotifyPayloadError:
        return None


def _mapping(value: object) -> Mapping[str, object] | None:
    """Return a typed mapping when an API field is a JSON object."""
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _mapping_or_error(value: object) -> Mapping[str, object]:
    """Return a JSON object field or raise a response-validation error."""
    mapping = _mapping(value)
    if mapping is None:
        raise SpotifyPayloadError("Spotify response contained malformed metadata")
    return mapping


def _required_text(payload: Mapping[str, object], name: str) -> str:
    """Read a required non-empty string field from a Spotify object."""
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SpotifyPayloadError(f"Spotify response lacked {name}")
    return value


def _optional_text(payload: Mapping[str, object] | None, name: str) -> str | None:
    """Read an optional string field from a Spotify object."""
    value = payload.get(name) if payload is not None else None
    return value if isinstance(value, str) else None


def _optional_int(payload: Mapping[str, object], name: str) -> int | None:
    """Read an optional integer field, excluding boolean JSON values."""
    value = payload.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _release_year(value: object) -> int | None:
    """Extract a valid year from Spotify's partial release-date string."""
    if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        return None
    year = int(value[:4])
    return year if 1860 <= year <= 2100 else None
