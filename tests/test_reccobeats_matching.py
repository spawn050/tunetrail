"""Offline conservative matching against synthetic ReccoBeats responses."""

from uuid import UUID

from tunetrail.reccobeats.matching import match_search
from tunetrail.recommendation.independent_discovery import IndependentCandidate

ID = UUID("11111111-1111-4111-8111-111111111111")
RB_ID = UUID("22222222-2222-4222-8222-222222222222")


def candidate() -> IndependentCandidate:
    return IndependentCandidate(ID, "A Song", ("An Artist",), 180000)


def row(**changes: object) -> dict[str, object]:
    return {
        "id": str(RB_ID),
        "trackTitle": "A Song",
        "durationMs": 180500,
        "artists": [{"name": "An Artist", "href": "https://open.spotify.com/artist/secret"}],
        "href": "https://open.spotify.com/track/secret",
        **changes,
    }


def page(*items: object, **changes: object) -> dict[str, object]:
    return {
        "content": list(items),
        "totalElements": len(items),
        "totalPages": 1,
        "page": 0,
        **changes,
    }


def test_match_requires_unique_complete_and_consistent_evidence() -> None:
    assert match_search(candidate(), page(row())) == RB_ID
    assert match_search(candidate(), page(row(), row())) is None
    assert match_search(candidate(), page(row(), totalElements=2, totalPages=2)) is None
    assert match_search(candidate(), page(row(durationMs=183000))) is None
    assert match_search(candidate(), page(row(trackTitle="A Song (Live)"))) is None
    assert match_search(candidate(), page(row(artists=[{"name": "Namesake"}]))) is None
    assert (
        match_search(IndependentCandidate(ID, "A Song", ("An Artist",), None), page(row())) is None
    )


def test_malformed_page_cannot_prove_uniqueness() -> None:
    assert match_search(candidate(), page(row(), {})) is None
    assert match_search(candidate(), page(row(id="spotify:track:abc"))) is None
    assert match_search(candidate(), page(row(), totalElements=True)) is None
    assert match_search(candidate(), page(row(), page=1)) is None
