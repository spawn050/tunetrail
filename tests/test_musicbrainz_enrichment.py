"""Cache-only interactive MusicBrainz enrichment and bounded refresh."""

import asyncio
from collections.abc import Sequence
from typing import cast

from tests.factories import intent
from tests.test_request_graph import FakeParser, FakeSpotify, playable_candidate
from tunetrail.domain.models import Candidate
from tunetrail.domain.protocols import SpotifyGateway
from tunetrail.graph.models import ApprovalDecision, RequestOutcome
from tunetrail.graph.request_graph import NaturalLanguageRequestGraph
from tunetrail.musicbrainz.enrichment import MusicBrainzEnrichment
from tunetrail.musicbrainz.matching import RecordingMatch
from tunetrail.musicbrainz.models import Recording, Release


class Lookup:
    def __init__(self, match: RecordingMatch | None) -> None:
        self.match = match
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def cached(self, candidate: Candidate) -> RecordingMatch | None:
        return self.match

    async def refresh(self, candidate: Candidate) -> RecordingMatch | None:
        self.started.set()
        await self.release.wait()
        return None


def match(*releases: Release) -> RecordingMatch:
    return RecordingMatch(
        recording=Recording(id="recording", title="Love Story", releases=list(releases)),
        confidence=0.95,
        method="artist_title_duration",
    )


async def test_cached_year_is_explained_and_approval_required() -> None:
    spotify = FakeSpotify([playable_candidate(album="Fearless", year=None)])
    lookup = Lookup(match(Release(id="release", title="Fearless", date="2008-11-11")))
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(artists=["Taylor Swift"], year_start=2008, year_end=2008)),
        spotify=cast(SpotifyGateway, spotify),
        musicbrainz_enrichment=MusicBrainzEnrichment(lookup),
    )
    result = await graph.start("Taylor Swift 2008", device_id="device-1")
    assert result.outcome is RequestOutcome.PREVIEW_READY
    assert result.preview is not None
    assert "year from a matched MusicBrainz release" in result.preview.items[0].explanation
    assert spotify.started == []
    assert (await graph.resume(ApprovalDecision.CANCEL)).outcome is RequestOutcome.CANCELLED
    assert spotify.started == []


async def test_ambiguous_release_does_not_invent_year() -> None:
    lookup = Lookup(
        match(
            Release(id="one", title="Fearless", date="2008"),
            Release(id="two", title="Fearless", date="2021"),
        )
    )
    candidates = await MusicBrainzEnrichment(lookup).enrich(
        [playable_candidate(album="Fearless", year=None)]
    )
    assert candidates[0].year is None
    assert "musicbrainz_year_source" not in candidates[0].provenance


async def test_cache_miss_returns_without_network_wait() -> None:
    lookup = Lookup(None)
    service = MusicBrainzEnrichment(lookup)
    candidates = [playable_candidate()]
    result = await asyncio.wait_for(service.enrich(candidates), timeout=0.2)
    assert result == candidates
    await asyncio.wait_for(lookup.started.wait(), timeout=0.2)
    lookup.release.set()
    await asyncio.sleep(0)


async def test_metadata_failure_preserves_preview() -> None:
    class Failure:
        async def enrich(self, candidates: Sequence[Candidate]) -> Sequence[Candidate]:
            raise RuntimeError("cache unavailable")

    spotify = FakeSpotify([playable_candidate()])
    graph = NaturalLanguageRequestGraph(
        parser=FakeParser(intent(artists=["Taylor Swift"])),
        spotify=cast(SpotifyGateway, spotify),
        musicbrainz_enrichment=Failure(),
    )
    result = await graph.start("Taylor Swift", device_id="device-1")
    assert result.outcome is RequestOutcome.PREVIEW_READY
    assert not spotify.started
