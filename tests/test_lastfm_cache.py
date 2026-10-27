"""Tests for private normalized Last.fm cache persistence and expiry."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.factories import candidate
from tunetrail.domain.models import CandidateSource, LastfmProfile
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore


async def test_store_caches_bounded_normalized_lastfm_profile_and_candidates(
    tmp_path: Path,
) -> None:
    """The store keeps typed enrichment signals rather than raw Last.fm responses."""
    store = SQLitePersonalizationStore(tmp_path / "tunetrail.sqlite3")
    now = datetime.now(UTC)
    profile = LastfmProfile(
        top_artists=["Artist"],
        top_tracks=[candidate(source=CandidateSource.LASTFM, source_id="artist:track")],
        updated_at=now,
    )
    candidates = [candidate(source=CandidateSource.LASTFM, source_id="artist:similar")]

    await store.save_lastfm_profile(profile, refreshed_at=now, expires_at=now + timedelta(hours=6))
    await store.save_lastfm_candidates(
        "lastfm:similar:artist:track",
        candidates,
        refreshed_at=now,
        expires_at=now + timedelta(hours=1),
    )

    cached_profile = await store.lastfm_profile(now=now)
    cached_candidates = await store.lastfm_candidates("lastfm:similar:artist:track", now=now)

    assert cached_profile == profile
    assert list(cached_candidates or []) == candidates


async def test_lastfm_cache_expiry_and_reset_remove_optional_source_data(tmp_path: Path) -> None:
    """Stale Last.fm data is never returned and reset deletes all application-owned cache state."""
    store = SQLitePersonalizationStore(tmp_path / "tunetrail.sqlite3")
    now = datetime.now(UTC)
    profile = LastfmProfile(updated_at=now)
    await store.save_lastfm_profile(
        profile, refreshed_at=now, expires_at=now + timedelta(minutes=1)
    )

    assert await store.lastfm_profile(now=now + timedelta(minutes=2)) is None
    await store.reset()

    assert await store.lastfm_profile(now=now) is None
