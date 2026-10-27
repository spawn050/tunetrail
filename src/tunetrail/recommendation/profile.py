"""Build compact local profiles from Spotify-owned listening signals."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from tunetrail.domain.models import (
    CatalogItem,
    CatalogItemKind,
    PlaylistSignal,
    ProfileSummary,
    ResolvedTrack,
)
from tunetrail.domain.protocols import PersonalizationStore, SpotifyGateway

_WINDOWS = ("short_term", "medium_term", "long_term")
_PROFILE_CACHE_TTL = timedelta(hours=12)
_MAX_PROFILE_ARTISTS = 50
_MAX_PROFILE_TRACKS = 100


class SpotifyProfileService:
    """Derive a bounded local taste profile without preserving source payloads."""

    def __init__(self, *, spotify: SpotifyGateway, store: PersonalizationStore) -> None:
        self._spotify = spotify
        self._store = store

    async def refresh(self) -> ProfileSummary:
        """Fetch Spotify signals and atomically replace the compact local profile summary."""
        now = datetime.now(UTC)
        signals = list(await self._store.playlist_signals())
        (
            top_tracks_by_window,
            artists_by_window,
            recent_tracks,
            playlist_tracks,
        ) = await asyncio.gather(
            self._top_tracks(),
            self._top_artists(),
            self._spotify.recently_played_tracks(),
            self._selected_playlist_tracks(signals),
        )
        top_tracks = _flatten_tracks(top_tracks_by_window)
        profile = ProfileSummary(
            favorite_artists=_unique(
                [
                    *[artist for artists in artists_by_window for artist in artists],
                    *[
                        artist
                        for track in [*top_tracks, *playlist_tracks]
                        for artist in track.artists
                    ],
                ]
            )[:_MAX_PROFILE_ARTISTS],
            favorite_track_uris=_unique(
                [track.spotify_uri for track in [*top_tracks, *playlist_tracks]]
            )[:_MAX_PROFILE_TRACKS],
            recent_track_uris=_unique([track.spotify_uri for track in recent_tracks])[
                :_MAX_PROFILE_TRACKS
            ],
            summary=_summary(top_tracks, recent_tracks, signals),
            updated_at=now,
        )
        refreshed_signals = [
            signal.model_copy(update={"last_ingested_at": now}) for signal in signals
        ]
        await self._store.save_profile_refresh(
            profile,
            refreshed_signals,
            refreshed_at=now,
            expires_at=now + _PROFILE_CACHE_TTL,
        )
        return profile

    async def _top_tracks(self) -> list[Sequence[ResolvedTrack]]:
        """Fetch all documented top-track affinity windows concurrently."""
        return list(
            await asyncio.gather(*(self._spotify.top_tracks(window) for window in _WINDOWS))
        )

    async def _top_artists(self) -> list[Sequence[str]]:
        """Fetch all documented top-artist affinity windows concurrently."""
        return list(
            await asyncio.gather(*(self._spotify.top_artists(window) for window in _WINDOWS))
        )

    async def _selected_playlist_tracks(self, signals: list[PlaylistSignal]) -> list[ResolvedTrack]:
        """Temporarily derive signals from selected playlist tracks, then discard their contents."""
        tracks_by_playlist = await asyncio.gather(
            *(
                self._spotify.tracks_for_catalog_item(
                    CatalogItem(
                        kind=CatalogItemKind.PLAYLIST,
                        spotify_id=signal.spotify_playlist_id,
                        spotify_uri=f"spotify:playlist:{signal.spotify_playlist_id}",
                        title=signal.display_name,
                    ),
                    limit=50,
                )
                for signal in signals
            )
        )
        return _flatten_tracks(tracks_by_playlist)


def _flatten_tracks(track_groups: Sequence[Sequence[ResolvedTrack]]) -> list[ResolvedTrack]:
    """Flatten source windows while retaining Spotify's affinity ordering within each group."""
    return [track for group in track_groups for track in group]


def _unique(values: Sequence[str]) -> list[str]:
    """Deduplicate signal values without changing their strongest-source-first ordering."""
    return list(dict.fromkeys(values))


def _summary(
    top_tracks: Sequence[ResolvedTrack],
    recent_tracks: Sequence[ResolvedTrack],
    signals: Sequence[PlaylistSignal],
) -> str:
    """Produce an inspectable local summary without sending listening data to an LLM."""
    return (
        f"Spotify profile from {len(top_tracks)} top-track signals, "
        f"{len(recent_tracks)} recent-play signals, and {len(signals)} opted-in playlists."
    )
