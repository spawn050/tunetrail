"""Tests for compact local Spotify profile construction."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from tests.factories import resolved_track
from tunetrail.domain.models import CatalogItem, PlaylistSignal, ProfileSummary, ResolvedTrack
from tunetrail.domain.protocols import PersonalizationStore, SpotifyGateway
from tunetrail.recommendation.profile import SpotifyProfileService


class FakeStore:
    """Capture profile writes without creating a database for service behavior tests."""

    def __init__(self, signals: Sequence[PlaylistSignal]) -> None:
        self.signals = signals
        self.profile: ProfileSummary | None = None
        self.refreshed_signals: Sequence[PlaylistSignal] = []

    async def playlist_signals(self) -> Sequence[PlaylistSignal]:
        """Return the selected playlist references."""
        return self.signals

    async def save_profile_refresh(
        self,
        profile: ProfileSummary,
        signals: Sequence[PlaylistSignal],
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Capture the atomically supplied values for assertions."""
        assert expires_at - refreshed_at == timedelta(hours=12)
        self.profile = profile
        self.refreshed_signals = signals


class FakeSpotify:
    """Return bounded Spotify signal data without network access."""

    def __init__(self) -> None:
        self.playlist_reads: list[CatalogItem] = []

    async def top_tracks(self, time_range: str, *, limit: int = 50) -> Sequence[ResolvedTrack]:
        """Return a distinct top track for each requested affinity window."""
        return [
            resolved_track(
                spotify_uri=f"spotify:track:{time_range.replace('_', '')}", artists=["Top Artist"]
            )
        ]

    async def top_artists(self, time_range: str, *, limit: int = 50) -> Sequence[str]:
        """Return a repeated plus a distinct artist to validate profile deduplication."""
        return ["Top Artist", f"{time_range} Artist"]

    async def recently_played_tracks(self, *, limit: int = 50) -> Sequence[ResolvedTrack]:
        """Return a recent URI that should become a repetition signal."""
        return [resolved_track(spotify_uri="spotify:track:recent", artists=["Recent Artist"])]

    async def tracks_for_catalog_item(
        self, item: CatalogItem, *, limit: int = 10
    ) -> Sequence[ResolvedTrack]:
        """Record the opted-in playlist read and return a temporary taste signal."""
        self.playlist_reads.append(item)
        return [resolved_track(spotify_uri="spotify:track:playlist", artists=["Playlist Artist"])]


async def test_profile_refresh_merges_bounded_spotify_signals_and_playlist_opt_ins() -> None:
    """Profile construction stores summaries, not raw endpoint or playlist payloads."""
    selected_at = datetime.now(UTC)
    store = FakeStore(
        [
            PlaylistSignal(
                spotify_playlist_id="playlist-1",
                display_name="Study signals",
                selected_at=selected_at,
            )
        ]
    )
    spotify = FakeSpotify()
    service = SpotifyProfileService(
        spotify=cast(SpotifyGateway, spotify), store=cast(PersonalizationStore, store)
    )

    profile = await service.refresh()

    assert profile.favorite_artists == [
        "Top Artist",
        "short_term Artist",
        "medium_term Artist",
        "long_term Artist",
        "Playlist Artist",
    ]
    assert profile.favorite_track_uris[-1] == "spotify:track:playlist"
    assert profile.recent_track_uris == ["spotify:track:recent"]
    assert spotify.playlist_reads[0].spotify_id == "playlist-1"
    assert store.refreshed_signals[0].last_ingested_at is not None
