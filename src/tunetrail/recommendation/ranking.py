"""Transparent Spotify-only ranking, diversity, and queue fitting rules."""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from tunetrail.domain.models import (
    Candidate,
    FamiliarityPreference,
    Feedback,
    FeedbackKind,
    ListeningIntent,
    PlaybackAction,
    ProfileSummary,
    QueueItem,
    QueuePreview,
    ResolvedTrack,
)


class PersonalizationClarification(ValueError):
    """Raised when a request cannot be fulfilled safely with local Spotify signals."""


def validate_personalized_intent(intent: ListeningIntent, profile: ProfileSummary | None) -> None:
    """Accept Phase 4 requests only when their local Spotify basis is explicit and available."""
    if intent.clarification_needed:
        raise PersonalizationClarification(
            "Please clarify the music request before TuneTrail searches."
        )
    if intent.action not in {PlaybackAction.PLAY_NOW, PlaybackAction.APPEND_QUEUE}:
        raise PersonalizationClarification("For now, ask TuneTrail to play or append music.")
    if not _has_direct_constraint(intent) and profile is None:
        raise PersonalizationClarification(
            "Refresh your Spotify taste profile before requesting personalized music."
        )


def rank_personalized_candidates(
    candidates: Sequence[Candidate],
    intent: ListeningIntent,
    *,
    profile: ProfileSummary | None,
    feedback: Sequence[Feedback],
    generated_uris: Sequence[str],
) -> list[ResolvedTrack]:
    """Hard-filter then greedily rank Spotify candidates with visible deterministic scores."""
    validate_personalized_intent(intent, profile)
    favorite_uris = set(profile.favorite_track_uris if profile is not None else [])
    favorite_artists: set[str] = (
        {_normal(value) for value in profile.favorite_artists} if profile else set()
    )
    recent_uris = set(profile.recent_track_uris if profile is not None else []) | set(
        generated_uris
    )
    feedback_by_uri = _feedback_by_uri(feedback)
    scored = [
        track
        for candidate in candidates
        if (
            track := _resolve_and_score(
                candidate,
                intent,
                favorite_uris=favorite_uris,
                favorite_artists=favorite_artists,
                recent_uris=recent_uris,
                feedback_by_uri=feedback_by_uri,
            )
        )
        is not None
    ]
    return _select_diverse_duration_fit(scored, intent)


def build_personalized_queue_preview(
    intent: ListeningIntent, tracks: Sequence[ResolvedTrack]
) -> QueuePreview:
    """Build an approval-ready personalized preview without mutating Spotify playback."""
    if not tracks:
        raise PersonalizationClarification(
            "No playable Spotify tracks matched that request and your local taste signals. "
            "Try changing the constraints or refresh your profile."
        )
    return QueuePreview(
        intent=intent,
        items=[QueueItem(track=track, explanation=_explanation(intent, track)) for track in tracks],
        created_at=datetime.now(UTC),
    )


def _resolve_and_score(
    candidate: Candidate,
    intent: ListeningIntent,
    *,
    favorite_uris: set[str],
    favorite_artists: set[str],
    recent_uris: set[str],
    feedback_by_uri: dict[str, set[FeedbackKind]],
) -> ResolvedTrack | None:
    """Construct a playable track only after all transparent Phase 4 hard filters pass."""
    uri = candidate.provenance.get("spotify_uri")
    if (
        uri is None
        or not uri.startswith("spotify:track:")
        or candidate.duration_ms is None
        or candidate.explicit is None
        or not _matches_hard_constraints(candidate, intent)
    ):
        return None
    feedback_kinds = feedback_by_uri.get(uri, set())
    if FeedbackKind.DISLIKE in feedback_kinds or FeedbackKind.LESS_LIKE_THIS in feedback_kinds:
        return None
    known = uri in favorite_uris
    artist_affinity = any(_normal(artist) in favorite_artists for artist in candidate.artists)
    scores = _base_scores(
        candidate,
        intent,
        known=known,
        artist_affinity=artist_affinity,
        recently_seen=uri in recent_uris,
        feedback_kinds=feedback_kinds,
    )
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
        score_breakdown=scores,
    )


def _matches_hard_constraints(candidate: Candidate, intent: ListeningIntent) -> bool:
    """Apply artist, title, year, explicit-content, and playable-URI requirements."""
    if intent.artists and not all(
        any(
            _normal(candidate_artist) == _normal(requested)
            for candidate_artist in candidate.artists
        )
        for requested in intent.artists
    ):
        return False
    if intent.track_terms and not all(
        _normal(term) in _normal(candidate.title) for term in intent.track_terms
    ):
        return False
    if intent.year_start is not None and (
        candidate.year is None or candidate.year < intent.year_start
    ):
        return False
    if intent.year_end is not None and (candidate.year is None or candidate.year > intent.year_end):
        return False
    return intent.explicit_allowed is not False or not candidate.explicit


def _base_scores(
    candidate: Candidate,
    intent: ListeningIntent,
    *,
    known: bool,
    artist_affinity: bool,
    recently_seen: bool,
    feedback_kinds: set[FeedbackKind],
) -> dict[str, float]:
    """Calculate non-diversity score components independently for inspection and testing."""
    request_fit = float(
        bool(intent.artists) + bool(intent.track_terms) + bool(intent.year_start or intent.year_end)
    )
    taste_affinity = (3.0 if known else 0.0) + (2.0 if artist_affinity else 0.0)
    familiarity = _familiarity_score(intent.familiarity, known)
    feedback_score = 0.0
    if FeedbackKind.LIKE in feedback_kinds:
        feedback_score += 4.0
    if FeedbackKind.MORE_LIKE_THIS in feedback_kinds:
        feedback_score += 2.0
    if FeedbackKind.TOO_FAMILIAR in feedback_kinds:
        feedback_score -= 3.0
    if FeedbackKind.TOO_DISTRACTING in feedback_kinds:
        feedback_score -= 4.0
    mood_activity = _tag_compatibility(candidate.tags, intent)
    return {
        "request_fit": request_fit,
        "taste_affinity": taste_affinity,
        "familiarity": familiarity,
        "feedback": feedback_score,
        "mood_activity_compatibility": mood_activity,
        "recent_play_penalty": -4.0 if recently_seen else 0.0,
        "source_confidence": candidate.source_confidence,
    }


def _select_diverse_duration_fit(
    tracks: Sequence[ResolvedTrack], intent: ListeningIntent
) -> list[ResolvedTrack]:
    """Greedily choose a diverse queue that fits requested duration where practical."""
    selected: list[ResolvedTrack] = []
    selected_artists: set[str] = set()
    selected_albums: set[str] = set()
    elapsed_ms = 0
    target_ms = intent.duration_minutes * 60_000 if intent.duration_minutes is not None else None
    remaining = list(tracks)
    while remaining and len(selected) < intent.queue_size:
        ranked = sorted(
            remaining,
            key=lambda track: _selection_score(track, selected_artists, selected_albums),
            reverse=True,
        )
        duration_compatible = [
            track
            for track in ranked
            if target_ms is None or not selected or elapsed_ms + track.duration_ms <= target_ms
        ]
        chosen = next(
            (
                track
                for track in duration_compatible
                if _is_diverse(track, selected_artists, selected_albums)
            ),
            duration_compatible[0] if duration_compatible else None,
        )
        if chosen is None:
            break
        remaining.remove(chosen)
        artist_duplicate = any(_normal(artist) in selected_artists for artist in chosen.artists)
        album_duplicate = chosen.album is not None and _normal(chosen.album) in selected_albums
        score_breakdown = dict(chosen.score_breakdown)
        score_breakdown["artist_diversity"] = -3.0 if artist_duplicate else 1.0
        score_breakdown["album_diversity"] = -1.0 if album_duplicate else 0.0
        selected.append(chosen.model_copy(update={"score_breakdown": score_breakdown}))
        selected_artists.update(_normal(artist) for artist in chosen.artists)
        if chosen.album is not None:
            selected_albums.add(_normal(chosen.album))
        elapsed_ms += chosen.duration_ms
    return selected


def _selection_score(
    track: ResolvedTrack, selected_artists: set[str], selected_albums: set[str]
) -> float:
    """Apply diversity at selection time so the earlier queue choices alter later scores."""
    score = sum(track.score_breakdown.values())
    if any(_normal(artist) in selected_artists for artist in track.artists):
        score -= 3.0
    if track.album is not None and _normal(track.album) in selected_albums:
        score -= 1.0
    return score


def _is_diverse(
    track: ResolvedTrack, selected_artists: set[str], selected_albums: set[str]
) -> bool:
    """Prefer unique artists and albums whenever the candidate set makes that possible."""
    return not any(_normal(artist) in selected_artists for artist in track.artists) and (
        track.album is None or _normal(track.album) not in selected_albums
    )


def _familiarity_score(preference: FamiliarityPreference, known: bool) -> float:
    """Favor known or unknown tracks according to the listener's explicit preference."""
    if preference is FamiliarityPreference.FAMILIAR:
        return 3.0 if known else -2.0
    if preference is FamiliarityPreference.DISCOVERY:
        return -2.0 if known else 3.0
    return 1.0 if known else 0.5


def _feedback_by_uri(feedback: Iterable[Feedback]) -> dict[str, set[FeedbackKind]]:
    """Group append-only feedback events for clear local preference precedence."""
    result: dict[str, set[FeedbackKind]] = {}
    for item in feedback:
        result.setdefault(item.spotify_uri, set()).add(item.kind)
    return result


def _explanation(intent: ListeningIntent, track: ResolvedTrack) -> str:
    """Explain actual local score and external-source evidence without claiming Spotify metadata."""
    parts = [
        name.replace("_", " ")
        for name, score in track.score_breakdown.items()
        if score > 0 and name not in {"source_confidence", "artist_diversity", "album_diversity"}
    ]
    candidate = track.candidates[0]
    if track.score_breakdown.get("mood_activity_compatibility", 0.0) > 0:
        parts.append("Last.fm community tags match the requested mood/activity")
    elif intent.moods or intent.activities:
        parts.append("Spotify-only profile match; mood/activity metadata is not inferred")
    if "lastfm_url" in candidate.provenance or "lastfm_method" in candidate.provenance:
        parts.append("Last.fm similarity evidence resolved to Spotify")
    if "musicbrainz_year_source" in candidate.provenance:
        parts.append("year from a matched MusicBrainz release")
    if not parts:
        parts.append("valid Spotify result from your local profile seeds")
    return f"Selected for {', '.join(parts)}."


def _has_direct_constraint(intent: ListeningIntent) -> bool:
    """Identify requests that can safely run without a pre-existing local profile."""
    return bool(
        intent.artists
        or intent.track_terms
        or intent.year_start is not None
        or intent.year_end is not None
    )


def _normal(value: str) -> str:
    """Normalize local comparison values without altering visible Spotify metadata."""
    return " ".join(value.casefold().split())


def _tag_compatibility(tags: Sequence[str], intent: ListeningIntent) -> float:
    """Score normalized Last.fm community tags against explicit mood/activity request terms."""
    requested = {_normal(term) for term in [*intent.moods, *intent.activities]}
    normalized_tags = {_normal(tag) for tag in tags}
    matches = {
        term
        for term in requested
        if any(term == tag or term in tag or tag in term for tag in normalized_tags)
    }
    return float(len(matches) * 2)
