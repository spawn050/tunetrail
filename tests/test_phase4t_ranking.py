from datetime import UTC, datetime
from uuid import UUID

from tunetrail.domain.models import (
    ExplicitPreference,
    ListeningIntent,
    PreferenceSource,
    PreferenceSubject,
)
from tunetrail.recommendation.independent_discovery import (
    EnrichedCandidate,
    FeatureEstimate,
    IndependentCandidate,
)
from tunetrail.recommendation.ranking import rank_candidates


def item(number: int, artist: str, energy: float | None = None) -> EnrichedCandidate:
    candidate = IndependentCandidate(UUID(int=number), f"Song {number}", (artist,), 180000)
    feature = FeatureEstimate(UUID(int=100), (("energy", energy),)) if energy is not None else None
    return EnrichedCandidate(candidate, feature, "available" if feature else "unavailable")


def pref(subject: PreferenceSubject, name: str, strength: int) -> ExplicitPreference:
    return ExplicitPreference(
        subject=subject,
        name=name,
        strength=strength,
        source=PreferenceSource.DIRECT,
        updated_at=datetime.now(UTC),
    )


def test_ranking_explains_actual_signals_and_diversity() -> None:
    choices = [item(2, "Artist", 0.9), item(1, "Artist", 0.9), item(3, "Other")]
    result = rank_candidates(
        choices,
        [pref(PreferenceSubject.ARTIST, "Artist", 1), pref(PreferenceSubject.FEATURE, "energy", 2)],
        ListeningIntent(),
    )
    assert [x.item.candidate.recording_id.int for x in result.candidates] == [1, 2, 3]
    assert result.candidates[0].score == 1.8
    assert ("repeated artist in preview", -0.5) in result.candidates[1].signals
    assert result.candidates[2].signals == ()
    assert (
        rank_candidates(
            list(reversed(choices)),
            [
                pref(PreferenceSubject.ARTIST, "Artist", 1),
                pref(PreferenceSubject.FEATURE, "energy", 2),
            ],
            ListeningIntent(),
        )
        == result
    )


def test_unsupported_constraints_and_duplicate_recordings() -> None:
    result = rank_candidates(
        [item(1, "A"), item(1, "A")], [], ListeningIntent(year_start=2000, explicit_allowed=False)
    )
    assert len(result.candidates) == 1
    assert len(result.unsupported) == 2
