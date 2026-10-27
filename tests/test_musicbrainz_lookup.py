"""ISRC-first canonical lookup behavior."""

from collections.abc import Sequence

import pytest

from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.musicbrainz.client import MusicBrainzError
from tunetrail.musicbrainz.lookup import lookup_recording
from tunetrail.musicbrainz.models import Artist, Recording


class Lookup:
    def __init__(self, by_isrc: Sequence[Recording], search: Sequence[Recording]) -> None:
        self.by_isrc_result = by_isrc
        self.search_result = search
        self.calls: list[str] = []

    async def by_isrc(self, isrc: str) -> Sequence[Recording]:
        self.calls.append("isrc")
        return self.by_isrc_result

    async def search(self, *, artist: str, title: str) -> Sequence[Recording]:
        self.calls.append("search")
        return self.search_result


def make_candidate() -> Candidate:
    return Candidate(
        source=CandidateSource.SPOTIFY,
        source_id="1",
        title="Song",
        artists=["Artist"],
        isrc="USABC1234567",
    )


def make_recording(identifier: str) -> Recording:
    return Recording(
        id=identifier, title="Song", artists=[Artist(id="a", name="Artist")], isrcs=["USABC1234567"]
    )


async def test_isrc_match_and_empty_fallback() -> None:
    direct = Lookup([make_recording("one")], [])
    match = await lookup_recording(make_candidate(), direct)
    assert match is not None and match.method == "isrc"
    assert direct.calls == ["isrc"]
    fallback = Lookup([], [make_recording("one")])
    assert await lookup_recording(make_candidate(), fallback) is not None
    assert fallback.calls == ["isrc", "search"]


async def test_ambiguous_isrc_does_not_fall_back() -> None:
    lookup = Lookup([make_recording("one"), make_recording("two")], [make_recording("one")])
    assert await lookup_recording(make_candidate(), lookup) is None
    assert lookup.calls == ["isrc"]


async def test_failure_is_not_treated_as_a_cacheable_miss() -> None:
    class Failing(Lookup):
        async def by_isrc(self, isrc: str) -> Sequence[Recording]:
            raise MusicBrainzError("unavailable")

    with pytest.raises(MusicBrainzError):
        await lookup_recording(make_candidate(), Failing([], []))
