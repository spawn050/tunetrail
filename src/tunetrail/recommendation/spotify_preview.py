"""Resolve independent ranks to reviewable, non-mutating Spotify proposals."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from tunetrail.auth.service import SpotifyAuthorizationError
from tunetrail.domain.models import Candidate, CandidateSource, PlaybackAction
from tunetrail.recommendation.ranking import RankedCandidate, RankingResult
from tunetrail.spotify.approval import PlayerAction, PlayerApprovals
from tunetrail.spotify.errors import SpotifyApiError
from tunetrail.spotify.models import SpotifyPayloadError


class PlayableSearch(Protocol):
    async def search_playable_tracks(
        self, query: str, *, limit: int = 10
    ) -> Sequence[Candidate]: ...


@dataclass(frozen=True, slots=True)
class SpotifyMatch:
    """Spotify metadata remains separate from the independent ranking input."""

    ranked: RankedCandidate
    uri: str
    title: str
    artists: tuple[str, ...]
    duration_ms: int
    evidence: str


@dataclass(frozen=True, slots=True)
class PlaybackPreview:
    matches: tuple[SpotifyMatch, ...]
    issues: tuple[str, ...]
    proposal_id: str | None
    action: PlayerAction | None


async def build_preview(
    ranking: RankingResult,
    spotify: PlayableSearch,
    approvals: PlayerApprovals,
    *,
    action: PlaybackAction,
    device_id: str | None = None,
) -> PlaybackPreview:
    """Perform read-only search; no proposal if constraints or action are unsupported."""
    issues = list(ranking.unsupported)
    if action not in (PlaybackAction.PLAY_NOW, PlaybackAction.APPEND_QUEUE):
        issues.append("action is not supported by this preview")
    matches: list[SpotifyMatch] = []
    used: set[str] = set()
    for ranked in ranking.candidates:
        independent = ranked.item.candidate
        if independent.duration_ms is None:
            issues.append(f"{independent.title}: duration unavailable for matching")
            continue
        query = f'artist:"{_quote(independent.artists[0])}" track:"{_quote(independent.title)}"'
        try:
            results = await spotify.search_playable_tracks(query, limit=10)
        except (
            SpotifyApiError,
            SpotifyPayloadError,
            SpotifyAuthorizationError,
            httpx.HTTPError,
            TimeoutError,
        ):
            issues.append(f"{independent.title}: Spotify search unavailable")
            continue
        matching = [r for r in results if _matches(ranked, r)]
        if len(matching) != 1:
            issues.append(f"{independent.title}: no unique playable Spotify match")
            continue
        found = matching[0]
        uri = found.provenance["spotify_uri"]
        if uri in used:
            issues.append(f"{independent.title}: duplicate Spotify track")
            continue
        used.add(uri)
        assert found.duration_ms is not None
        matches.append(
            SpotifyMatch(
                ranked,
                uri,
                found.title,
                tuple(found.artists),
                found.duration_ms,
                "Exact normalized title and ordered artists; duration within 2 seconds; "
                "Spotify reports playable.",
            )
        )
    if len(matches) < len(ranking.candidates):
        issues.append("Fewer Spotify matches than ranked candidates")
    if action == PlaybackAction.APPEND_QUEUE and len(matches) > 1:
        issues.append("Queue append requires a separate preview and approval for each track")
        return PlaybackPreview(tuple(matches), tuple(issues), None, None)
    # Unsupported ranking constraints prevent authorization, even if a partial preview is useful.
    if (
        not matches
        or ranking.unsupported
        or action not in (PlaybackAction.PLAY_NOW, PlaybackAction.APPEND_QUEUE)
    ):
        return PlaybackPreview(tuple(matches), tuple(issues), None, None)
    params: dict[str, str] = {"device_id": device_id} if device_id is not None else {}
    if action == PlaybackAction.PLAY_NOW:
        request = PlayerAction.create(
            "PUT", "/me/player/play", params, {"uris": [m.uri for m in matches]}
        )
    else:
        params["uri"] = matches[0].uri
        request = PlayerAction.create("POST", "/me/player/queue", params, None)
    return PlaybackPreview(tuple(matches), tuple(issues), approvals.propose(request), request)


def _matches(ranked: RankedCandidate, result: Candidate) -> bool:
    candidate = ranked.item.candidate
    uri = result.provenance.get("spotify_uri", "")
    return (
        result.source == CandidateSource.SPOTIFY
        and re.fullmatch(r"spotify:track:[A-Za-z0-9]+", uri) is not None
        and _normal(candidate.title) == _normal(result.title)
        and tuple(map(_normal, candidate.artists)) == tuple(map(_normal, result.artists))
        and candidate.duration_ms is not None
        and result.duration_ms is not None
        and abs(candidate.duration_ms - result.duration_ms) <= 2000
    )


def _normal(value: str) -> str:
    return " ".join(value.casefold().split())


def _quote(value: str) -> str:
    return value.replace('"', " ").replace("\\", " ").strip()
