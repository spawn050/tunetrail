"""Conservative resolution of optional external suggestions to Spotify tracks."""

from tunetrail.domain.models import Candidate
from tunetrail.domain.protocols import SpotifyGateway

_MINIMUM_MATCH_CONFIDENCE = 0.85


class SpotifyCandidateResolver:
    """Resolve an external artist/title suggestion only when Spotify evidence is strong."""

    def __init__(self, spotify: SpotifyGateway) -> None:
        self._spotify = spotify

    async def resolve(self, candidate: Candidate) -> Candidate | None:
        """Map an external candidate to Spotify metadata while preserving source provenance."""
        query = f'artist:"{_quoted(candidate.artists[0])}" track:"{_quoted(candidate.title)}"'
        results = await self._spotify.search_tracks(query, limit=10)
        best = max(results, key=lambda result: _match_confidence(candidate, result), default=None)
        if best is None:
            return None
        confidence = _match_confidence(candidate, best)
        spotify_uri = best.provenance.get("spotify_uri")
        if confidence < _MINIMUM_MATCH_CONFIDENCE or spotify_uri is None:
            return None
        provenance = {
            **candidate.provenance,
            "spotify_uri": spotify_uri,
            "spotify_match_confidence": f"{confidence:.2f}",
        }
        return candidate.model_copy(
            update={
                "source_id": best.source_id,
                "title": best.title,
                "artists": best.artists,
                "album": best.album,
                "duration_ms": best.duration_ms,
                "year": best.year,
                "explicit": best.explicit,
                "source_confidence": round(candidate.source_confidence * confidence, 3),
                "provenance": provenance,
            }
        )


def _match_confidence(external: Candidate, spotify: Candidate) -> float:
    """Score conservative normalized artist/title/duration agreement between two candidates."""
    title_score = _text_match(external.title, spotify.title)
    artist_score = max(
        (
            _text_match(external_artist, spotify_artist)
            for external_artist in external.artists
            for spotify_artist in spotify.artists
        ),
        default=0.0,
    )
    if title_score < 0.9 or artist_score < 0.9:
        return 0.0
    duration_score = _duration_match(external.duration_ms, spotify.duration_ms)
    if duration_score == 0.0:
        return 0.0
    return 0.6 * title_score + 0.35 * artist_score + 0.05 * duration_score


def _text_match(left: str, right: str) -> float:
    """Require exact normalized metadata; ambiguous editions require further evidence."""
    normalized_left = _normal(left)
    normalized_right = _normal(right)
    if normalized_left and normalized_left == normalized_right:
        return 1.0
    return 0.0


def _duration_match(external: int | None, spotify: int | None) -> float:
    """Reward close duration evidence but avoid rejecting Last.fm records that omit it."""
    if external is None or spotify is None:
        return 1.0
    return 1.0 if abs(external - spotify) <= 10_000 else 0.0


def _normal(value: str) -> str:
    """Normalize punctuation and whitespace solely for conservative resolver comparisons."""
    return " ".join("".join(char if char.isalnum() else " " for char in value.casefold()).split())


def _quoted(value: str) -> str:
    """Keep Last.fm artist/title metadata inside one safe Spotify field-query value."""
    return value.replace('"', " ").strip()
