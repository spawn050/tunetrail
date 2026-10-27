"""Cache-first MusicBrainz metadata enrichment for Spotify candidates."""

import asyncio
from collections.abc import Sequence
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
            self._task = asyncio.create_task(self._refresh(misses[:3]))
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
        and album
        and not any(marker in album for marker in ("remaster", "deluxe", "live", "anniversary"))
    ):
        releases = [release for release in recording.releases if _normal(release.title) == album]
        years = {
            int(release.date[:4])
            for release in releases
            if release.date
            and len(release.date) >= 4
            and release.date[:4].isdigit()
            and 1860 <= int(release.date[:4]) <= 2100
        }
        if (
            releases
            and len(years) == 1
            and all(
                release.date
                and len(release.date) >= 4
                and release.date[:4].isdigit()
                and 1860 <= int(release.date[:4]) <= 2100
                for release in releases
            )
        ):
            year = next(iter(years))
            provenance["musicbrainz_year_source"] = "matching release"
    return candidate.model_copy(update={"year": year, "provenance": provenance})


def _normal(value: str) -> str:
    return " ".join("".join(char if char.isalnum() else " " for char in value.casefold()).split())
