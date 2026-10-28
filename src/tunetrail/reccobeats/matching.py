"""Conservative ReccoBeats search matching without exporting its metadata."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from tunetrail.recommendation.independent_discovery import IndependentCandidate


def match_search(candidate: IndependentCandidate, payload: object) -> UUID | None:
    """Match exactly one result on title, all credited artists and duration.

    Incomplete pages and malformed results cannot establish uniqueness. Spotify URLs,
    ISRCs and all other provider fields are ignored and are never returned.
    """
    if not isinstance(payload, dict):
        return None
    data = cast(Mapping[str, object], payload)
    items = data.get("content")
    total, page, total_pages = data.get("totalElements"), data.get("page"), data.get("totalPages")
    if not isinstance(items, list):
        return None
    rows = cast(list[object], items)
    if (
        len(rows) > 10
        or type(total) is not int
        or total != len(rows)
        or type(page) is not int
        or page != 0
        or type(total_pages) is not int
        or total_pages not in (0, 1)
        or (total > 0 and total_pages != 1)
    ):
        return None
    if candidate.duration_ms is None:
        return None
    matches: list[UUID] = []
    for item in rows:
        if not isinstance(item, dict):
            return None
        row = cast(Mapping[str, object], item)
        title, artists, duration, identifier = (
            row.get("trackTitle"),
            row.get("artists"),
            row.get("durationMs"),
            row.get("id"),
        )
        if not isinstance(title, str) or not isinstance(artists, list) or type(duration) is not int:
            return None
        if not isinstance(identifier, str) or duration <= 0:
            return None
        try:
            track_id = UUID(identifier)
        except ValueError:
            return None
        names: list[str] = []
        for artist in cast(list[object], artists):
            if not isinstance(artist, dict):
                return None
            name = cast(Mapping[str, object], artist).get("name")
            if not isinstance(name, str) or not name.strip():
                return None
            names.append(_normalize(name))
        if (
            _normalize(title) == _normalize(candidate.title)
            and names == [_normalize(name) for name in candidate.artists]
            and abs(duration - candidate.duration_ms) <= 2000
        ):
            matches.append(track_id)
    return matches[0] if len(matches) == 1 else None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
