"""Conservative MusicBrainz identity and ambiguity checks."""

from tunetrail.domain.models import Candidate, CandidateSource
from tunetrail.musicbrainz.matching import match_recording
from tunetrail.musicbrainz.models import Artist, Recording


def candidate(**updates: object) -> Candidate:
    return Candidate.model_validate(
        {
            "source": CandidateSource.SPOTIFY,
            "source_id": "1",
            "title": "Song",
            "artists": ["Artist Alias"],
            "duration_ms": 180000,
            **updates,
        }
    )


def recording(**updates: object) -> Recording:
    return Recording.model_validate(
        {
            "id": "rec1",
            "title": "Song",
            "duration_ms": 180000,
            "artists": [Artist(id="a", name="Artist", aliases=["Artist Alias"])],
            **updates,
        }
    )


def test_alias_and_duration_matching() -> None:
    match = match_recording(candidate(), [recording()])
    assert match is not None and match.confidence == 0.95
    assert match_recording(candidate(), [recording(duration_ms=220000)]) is None
    assert match_recording(candidate(), [recording(title="Song (Live)")]) is None


def test_isrc_priority_and_conflicts() -> None:
    track = candidate(isrc="USABC1234567")
    matched = recording(title="Different title", isrcs=["USABC1234567"])
    match = match_recording(track, [recording(id="other"), matched])
    assert match is not None and match.method == "isrc"
    assert match_recording(track, [recording(isrcs=["USABC0000000"])]) is None
    assert match_recording(track, [matched, recording(id="rec2", isrcs=["USABC1234567"])]) is None


def test_duplicate_identity_is_not_ambiguous() -> None:
    assert match_recording(candidate(), [recording(), recording()]) is not None
    assert match_recording(candidate(), [recording(), recording(id="rec2")]) is None
