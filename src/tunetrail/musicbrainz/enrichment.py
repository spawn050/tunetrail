"""Cache-first MusicBrainz metadata enrichment for Spotify candidates."""

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from tunetrail.domain.models import Candidate
from tunetrail.musicbrainz.matching import RecordingMatch


class CachedLookup(Protocol):
    async def cached(self, candidate: Candidate) -> RecordingMatch | None: ...

    async def refresh(self, candidate: Candidate) -> RecordingMatch | None: ...


class MusicBrainzEnrichment:
    """Use only cached matches now and refresh a small set in a background task."""

    def __init__(self, lookup: CachedLookup) -> None:
        self._lookup = lookup
        self._task: asyncio.Task[None] | None = None
        self._cursor = 0

    async def enrich(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        """Do not wait for network, and never alter Spotify identity or playback metadata."""
        result: list[Candidate] = []
        misses: list[Candidate] = []
        for candidate in candidates:
            try:
                match = await self._lookup.cached(candidate)
            except Exception:
                match = None
            if match is None:
                misses.append(candidate)
                result.append(candidate)
            else:
                result.append(_apply(candidate, match))
        if misses and (self._task is None or self._task.done()):
            # Rotate through misses across requests so repeatedly unresolved early
            # candidates cannot starve later candidates forever.
            start = self._cursor % len(misses)
            rotated = misses[start:] + misses[:start]
            batch = rotated[:3]
            self._cursor = (start + len(batch)) % len(misses)
            self._task = asyncio.create_task(self._refresh(batch))
        return result

    async def _refresh(self, candidates: Sequence[Candidate]) -> None:
        for candidate in candidates:
            try:
                await self._lookup.refresh(candidate)
            except Exception:
                # Optional enrichment errors must not affect the active request.
                continue


def _apply(candidate: Candidate, match: RecordingMatch) -> Candidate:
    """Add provenance and fill missing year only for an unambiguous matching album."""
    recording = match.recording
    provenance = {
        **candidate.provenance,
        "musicbrainz_recording_id": recording.id,
        "musicbrainz_match_method": match.method,
        "musicbrainz_match_confidence": f"{match.confidence:.2f}",
    }
    year = candidate.year
    album = _normal(candidate.album or "")
    if (
        year is None
        and recording.releases_complete
        and album
        and not any(marker in album for marker in ("remaster", "deluxe", "live", "anniversary"))
    ):
        releases = [release for release in recording.releases if _normal(release.title) == album]
        # Distinct releases with the same title can be separate editions even if
        # their dates agree; don't infer which one Spotify meant.
        if len(releases) == 1 and (release_year := _release_year(releases[0].date)) is not None:
            year = release_year
            provenance["musicbrainz_year_source"] = "matching release"
    return candidate.model_copy(update={"year": year, "provenance": provenance})


def _normal(value: str) -> str:
    return " ".join("".join(char if char.isalnum() else " " for char in value.casefold()).split())


def _release_year(value: str | None) -> int | None:
    """Validate MusicBrainz's partial ISO dates without inventing missing components."""
    if value is None:
        return None
    try:
        if len(value) == 4 and value.isascii() and value.isdigit():
            year = int(value)
        elif len(value) == 7:
            year = date.fromisoformat(value + "-01").year
        elif len(value) == 10:
            year = date.fromisoformat(value).year
        else:
            return None
    except ValueError:
        return None
    return year if 1860 <= year <= 2100 else None
