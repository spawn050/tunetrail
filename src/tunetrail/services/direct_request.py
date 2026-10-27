"""Deterministic Spotify-only handling for clear Phase 3 requests."""

from collections.abc import Sequence
from datetime import UTC, datetime

from tunetrail.domain.models import (
    Candidate,
    ListeningIntent,
    PlaybackAction,
    QueueItem,
    QueuePreview,
    ResolvedTrack,
)


class DirectRequestClarification(ValueError):
    """Raised when a request needs user input before safe direct retrieval."""


def validate_direct_intent(intent: ListeningIntent) -> None:
    """Reject requests that require future personalization or unsupported actions."""
    if intent.clarification_needed:
        raise DirectRequestClarification(
            "Please clarify the artist, track, or year you want TuneTrail to use."
        )
    if intent.action not in {PlaybackAction.PLAY_NOW, PlaybackAction.APPEND_QUEUE}:
        raise DirectRequestClarification("For now, ask TuneTrail to play or append specific music.")
    if not intent.artists and not intent.track_terms:
        raise DirectRequestClarification(
            "Try a specific artist, track, or year range; personalized mood requests "
            "arrive in Phase 4."
        )
    if len(intent.artists) > 1:
        raise DirectRequestClarification("Please request one artist at a time for now.")
    if intent.moods or intent.activities or intent.duration_minutes is not None:
        raise DirectRequestClarification(
            "Mood, activity, and duration requests need Phase 4 personalization. "
            "Try a specific artist, track, or year range."
        )
    if intent.familiarity.value != "mixed":
        raise DirectRequestClarification(
            "Familiarity and discovery preferences need Phase 4 personalization."
        )
    if intent.queue_size > 10:
        raise DirectRequestClarification("For now, request at most 10 tracks at a time.")


def spotify_search_query(intent: ListeningIntent) -> str:
    """Build a constrained Spotify search query without accepting query operators verbatim."""
    validate_direct_intent(intent)
    terms: list[str] = []
    if intent.artists:
        terms.append(f'artist:"{_quoted(intent.artists[0])}"')
    for track_term in intent.track_terms:
        terms.append(f'track:"{_quoted(track_term)}"')
    if intent.year_start is not None and intent.year_end is not None:
        terms.append(f"year:{intent.year_start}-{intent.year_end}")
    elif intent.year_start is not None:
        terms.append(f"year:{intent.year_start}")
    elif intent.year_end is not None:
        terms.append(f"year:*-{intent.year_end}")
    return " ".join(terms)


def rank_direct_candidates(
    candidates: Sequence[Candidate], intent: ListeningIntent
) -> list[ResolvedTrack]:
    """Filter and stably rank direct Spotify candidates using transparent constraints."""
    validate_direct_intent(intent)
    resolved: list[ResolvedTrack] = []
    seen_uris: set[str] = set()
    for candidate in candidates:
        track = _resolve_spotify_candidate(candidate, intent)
        if track is None or track.spotify_uri in seen_uris:
            continue
        seen_uris.add(track.spotify_uri)
        resolved.append(track)
    resolved.sort(key=lambda track: track.score_breakdown["direct_constraint_match"], reverse=True)
    return resolved[: intent.queue_size]


def build_direct_queue_preview(
    intent: ListeningIntent, tracks: Sequence[ResolvedTrack]
) -> QueuePreview:
    """Build an approval-ready preview without making any Spotify player request."""
    if not tracks:
        raise DirectRequestClarification(
            "No playable Spotify tracks matched those constraints. Try a different artist, "
            "track, or year."
        )
    return QueuePreview(
        intent=intent,
        items=[QueueItem(track=track, explanation=_explanation(intent, track)) for track in tracks],
        created_at=datetime.now(UTC),
    )


def _resolve_spotify_candidate(
    candidate: Candidate, intent: ListeningIntent
) -> ResolvedTrack | None:
    """Resolve a Spotify search candidate only when its playback metadata is complete."""
    uri = candidate.provenance.get("spotify_uri")
    if (
        uri is None
        or not uri.startswith("spotify:track:")
        or candidate.duration_ms is None
        or candidate.explicit is None
        or not _matches_constraints(candidate, intent)
    ):
        return None
    score = _direct_score(candidate, intent)
    return ResolvedTrack(
        spotify_id=candidate.source_id,
        spotify_uri=uri,
        title=candidate.title,
        artists=candidate.artists,
        album=candidate.album,
        duration_ms=candidate.duration_ms,
        explicit=candidate.explicit,
        match_confidence=1.0,
        candidates=[candidate],
        score_breakdown={"direct_constraint_match": score},
    )


def _matches_constraints(candidate: Candidate, intent: ListeningIntent) -> bool:
    """Apply only direct, inspectable hard filters in this phase."""
    if intent.artists and not any(
        _normalized(artist) == _normalized(intent.artists[0]) for artist in candidate.artists
    ):
        return False
    if intent.track_terms and not any(
        _normalized(term) in _normalized(candidate.title) for term in intent.track_terms
    ):
        return False
    if intent.year_start is not None and (
        candidate.year is None or candidate.year < intent.year_start
    ):
        return False
    if intent.year_end is not None and (candidate.year is None or candidate.year > intent.year_end):
        return False
    return intent.explicit_allowed is not False or not candidate.explicit


def _direct_score(candidate: Candidate, intent: ListeningIntent) -> float:
    """Score exact constraints transparently, preserving Spotify result order on ties."""
    score = 0.0
    if intent.artists:
        score += 3.0
    if intent.track_terms:
        score += 3.0
    if intent.year_start is not None or intent.year_end is not None:
        score += 1.0
    if intent.explicit_allowed is False and not candidate.explicit:
        score += 1.0
    return score


def _explanation(intent: ListeningIntent, track: ResolvedTrack) -> str:
    """Explain direct selection without attributing subjective AI judgment."""
    constraints: list[str] = []
    if intent.artists:
        constraints.append(f"artist: {intent.artists[0]}")
    if intent.track_terms:
        constraints.append(f"track: {', '.join(intent.track_terms)}")
    if intent.year_start is not None or intent.year_end is not None:
        start = str(intent.year_start) if intent.year_start is not None else "before"
        end = str(intent.year_end) if intent.year_end is not None else "now"
        constraints.append(f"year: {start}–{end}")
    if intent.explicit_allowed is False:
        constraints.append("non-explicit")
    return f"Spotify result matching {', '.join(constraints)}."


def _quoted(value: str) -> str:
    """Remove Spotify query delimiters from untrusted parsed terms."""
    return value.replace('"', " ").strip()


def _normalized(value: str) -> str:
    """Normalize comparison strings without changing displayed Spotify metadata."""
    return " ".join(value.casefold().split())
