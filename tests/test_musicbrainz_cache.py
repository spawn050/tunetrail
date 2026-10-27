"""Normalized cache expiry, privacy, and reset."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.musicbrainz.cache import CachedRecordingLookup
from tunetrail.musicbrainz.models import Artist, Recording
from tunetrail.persistence.sqlite_store import SQLitePersonalizationStore


class FakeLookup:
    calls = 0

    async def by_isrc(self, isrc: str) -> list[Recording]:
        self.calls += 1
        return [
            Recording(
                id="rec", title="Song", isrcs=[isrc], artists=[Artist(id="artist", name="Artist")]
            )
        ]

    async def search(self, *, artist: str, title: str) -> list[Recording]:
        raise AssertionError("ISRC match should not trigger search")

    async def artist(self, mbid: str) -> Artist:
        raise AssertionError("ISRC match should not trigger artist lookup")


async def test_cache_persists_only_match_and_reset(tmp_path: Path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "store.sqlite")
    client = FakeLookup()
    service = CachedRecordingLookup(client, store)
    candidate = Candidate(
        source=CandidateSource.SPOTIFY,
        source_id="1",
        title="Song",
        artists=["Artist"],
        isrc="USABC1234567",
    )
    assert await service.cached(candidate) is None
    match = await service.refresh(candidate)
    assert match is not None
    assert await service.cached(candidate) == match
    assert client.calls == 1
    changed = candidate.model_copy(update={"duration_ms": 180000})
    assert await service.cached(changed) is None
    await store.reset()
    assert await service.cached(candidate) is None


async def test_expiry(tmp_path: Path) -> None:
    store = SQLitePersonalizationStore(tmp_path / "store.sqlite")
    lookup = FakeLookup()
    service = CachedRecordingLookup(lookup, store)
    candidate = Candidate(
        source=CandidateSource.SPOTIFY,
        source_id="1",
        title="Song",
        artists=["Artist"],
        isrc="USABC1234567",
    )
    match = await service.refresh(candidate)
    assert match is not None
    now = datetime.now(UTC)
    await store.save_musicbrainz_match(
        "key", match, refreshed_at=now, expires_at=now + timedelta(seconds=1)
    )
    assert await store.musicbrainz_match("key", now=now + timedelta(seconds=1)) is None
