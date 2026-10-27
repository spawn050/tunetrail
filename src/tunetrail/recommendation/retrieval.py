"""Spotify-only candidate retrieval derived from local profile seeds."""

from tunetrail.domain.models import Candidate, ListeningIntent, ProfileSummary
from tunetrail.domain.protocols import SpotifyGateway
from tunetrail.recommendation.ranking import (
    PersonalizationClarification,
    validate_personalized_intent,
)

_MAX_PROFILE_SEEDS = 10


class SpotifyProfileCandidateRetriever:
    """Search Spotify tracks from direct constraints and local profile artist seeds only."""

    def __init__(self, spotify: SpotifyGateway) -> None:
        self._spotify = spotify

    async def find(
        self, intent: ListeningIntent, profile: ProfileSummary | None
    ) -> list[Candidate]:
        """Retrieve a bounded, deduplicated candidate set without public-playlist fallback."""
        validate_personalized_intent(intent, profile)
        queries = _queries(intent, profile)
        if not queries:
            raise PersonalizationClarification(
                "Your Spotify taste profile has no artist seeds yet. Refresh it after "
                "listening to music."
            )
        candidates: list[Candidate] = []
        seen_ids: set[str] = set()
        for query in queries:
            for candidate in await self._spotify.search_tracks(query, limit=10):
                if candidate.source_id not in seen_ids:
                    seen_ids.add(candidate.source_id)
                    candidates.append(candidate)
        return candidates


def _queries(intent: ListeningIntent, profile: ProfileSummary | None) -> list[str]:
    """Build safe field-filtered Spotify queries from direct constraints or profile artists."""
    direct = _direct_query(intent)
    if direct:
        return [direct]
    if profile is None:
        return []
    return [
        f'artist:"{_quoted(artist)}"' for artist in profile.favorite_artists[:_MAX_PROFILE_SEEDS]
    ]


def _direct_query(intent: ListeningIntent) -> str:
    """Build a non-LLM-controlled Spotify search query for explicit request constraints."""
    terms: list[str] = []
    if intent.artists:
        terms.extend(f'artist:"{_quoted(artist)}"' for artist in intent.artists)
    terms.extend(f'track:"{_quoted(term)}"' for term in intent.track_terms)
    if intent.year_start is not None and intent.year_end is not None:
        terms.append(f"year:{intent.year_start}-{intent.year_end}")
    elif intent.year_start is not None:
        terms.append(f"year:{intent.year_start}")
    elif intent.year_end is not None:
        terms.append(f"year:*-{intent.year_end}")
    return " ".join(terms)


def _quoted(value: str) -> str:
    """Strip Spotify query delimiters from parsed-but-untrusted listener terms."""
    return value.replace('"', " ").strip()
