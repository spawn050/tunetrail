"""Deterministic ranking over independently sourced candidates only."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from tunetrail.domain.models import ExplicitPreference, ListeningIntent, PreferenceSubject
from tunetrail.recommendation.independent_discovery import EnrichedCandidate


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """Independent candidate and the actual additive signals used for its rank."""

    item: EnrichedCandidate
    score: float
    signals: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class RankingResult:
    candidates: tuple[RankedCandidate, ...]
    unsupported: tuple[str, ...]


def rank_candidates(
    candidates: Sequence[EnrichedCandidate],
    preferences: Sequence[ExplicitPreference],
    intent: ListeningIntent,
) -> RankingResult:
    """Rank by explicit evidence; diversity penalizes repeated primary artists in this result."""
    unsupported: list[str] = []
    if intent.year_start is not None or intent.year_end is not None:
        unsupported.append("year: independent release year unavailable")
    if intent.explicit_allowed is False:
        unsupported.append("explicit-content restriction: independent metadata unavailable")
    if intent.duration_minutes is not None:
        unsupported.append("total duration: not enforced")
    if intent.moods or intent.activities:
        unsupported.append("mood/activity: no validated feature mapping")
    if intent.familiarity.value != "mixed":
        unsupported.append("familiarity: no listening history is used")
    if intent.clarification_needed:
        unsupported.append("request requires clarification")
    if any(p.subject == PreferenceSubject.GENRE for p in preferences):
        unsupported.append("genre preferences: independent genre unavailable")
    if any(p.subject == PreferenceSubject.ARTIST and p.musicbrainz_id for p in preferences):
        unsupported.append("artist ID preferences: candidate artist IDs unavailable")
    if any(
        p.subject == PreferenceSubject.FEATURE and p.name.casefold() not in _UNIT_FEATURES
        for p in preferences
    ):
        unsupported.append("some feature preferences are unsupported")
    unique: dict[object, EnrichedCandidate] = {}
    conflicts: set[object] = set()
    for item in candidates:
        identifier = item.candidate.recording_id
        if identifier in unique and unique[identifier] != item:
            conflicts.add(identifier)
        else:
            unique[identifier] = item
    if conflicts:
        unsupported.append("conflicting independent candidates share a recording ID")
    pending: list[tuple[EnrichedCandidate, list[tuple[str, float]]]] = []
    for identifier, item in unique.items():
        candidate = item.candidate
        if candidate.source != "musicbrainz" or identifier in conflicts:
            continue
        signals: list[tuple[str, float]] = []
        for pref in preferences:
            if not pref.strength:
                continue
            matched = False
            if pref.subject == PreferenceSubject.RECORDING:
                matched = (
                    str(candidate.recording_id) == pref.musicbrainz_id.lower()
                    if pref.musicbrainz_id
                    else _norm(pref.name) == _norm(candidate.title)
                    and _norm(pref.artist or "") in map(_norm, candidate.artists)
                )
            elif pref.subject == PreferenceSubject.ARTIST and not pref.musicbrainz_id:
                matched = _norm(pref.name) in map(_norm, candidate.artists)
            elif (
                pref.subject == PreferenceSubject.FEATURE
                and item.features
                and item.features.source == "reccobeats"
            ):
                value = dict(item.features.values).get(pref.name.casefold())
                if (
                    value is not None
                    and pref.name.casefold() in _UNIT_FEATURES
                    and isfinite(value)
                    and 0 <= value <= 1
                ):
                    signal = round(pref.strength * (float(value) - 0.5), 4)
                    if signal:
                        signals.append((f"ReccoBeats {pref.name} preference", signal))
            if matched:
                signals.append(
                    (f"{pref.subject.value} preference: {pref.name}", float(pref.strength))
                )
        if intent.artists and any(
            _norm(artist) in map(_norm, candidate.artists) for artist in intent.artists
        ):
            signals.append(("requested artist", 1.0))
        if intent.track_terms and any(
            _norm(term) in _norm(candidate.title) for term in intent.track_terms
        ):
            signals.append(("requested title", 1.0))
        pending.append((item, signals))
    result: list[RankedCandidate] = []
    artist_counts: dict[str, int] = {}
    while pending and len(result) < intent.queue_size:

        def key(entry: tuple[EnrichedCandidate, list[tuple[str, float]]]) -> tuple[float, str]:
            artist = _norm(entry[0].candidate.artists[0])
            score = sum(value for _, value in entry[1]) - min(artist_counts.get(artist, 0), 2) * 0.5
            return (-score, str(entry[0].candidate.recording_id))

        item, signals = min(pending, key=key)
        pending.remove((item, signals))
        artist = _norm(item.candidate.artists[0])
        penalty = min(artist_counts.get(artist, 0), 2) * 0.5
        if penalty:
            signals.append(("repeated artist in preview", -penalty))
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        result.append(RankedCandidate(item, round(sum(v for _, v in signals), 4), tuple(signals)))
    return RankingResult(tuple(result), tuple(unsupported))


_UNIT_FEATURES = frozenset(
    {
        "acousticness",
        "danceability",
        "energy",
        "instrumentalness",
        "liveness",
        "speechiness",
        "valence",
    }
)


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())
