"""Synthetic recording labels for Last.fm-to-Spotify resolution regression tests.

These hand-authored labels are not independently verified catalog judgments.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from tests.factories import candidate
from tunetrail.domain.models import Candidate
from tunetrail.domain.protocols import SpotifyGateway
from tunetrail.recommendation.resolver import SpotifyCandidateResolver


class External(BaseModel):
    title: str
    artists: list[str]
    duration_ms: int | None = None


class Result(BaseModel):
    id: str
    title: str
    artist: str
    duration_ms: int | None = None


class Case(BaseModel):
    name: str
    external: External
    results: list[Result]
    correct_ids: list[str]
    expected_id: str | None


class FixtureSpotify:
    def __init__(self, results: Sequence[Candidate]) -> None:
        self.results = results

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        return self.results


async def test_labelled_resolution_cases() -> None:
    """Count accepted correct/incorrect and missed matches separately from expected outcomes."""
    data: object = json.loads(
        (Path(__file__).parent / "fixtures" / "lastfm_resolution_labels.json").read_text()
    )
    assert isinstance(data, list)
    cases = [Case.model_validate(row) for row in cast(list[object], data)]
    correct = incorrect = missed = 0
    for case in cases:
        external = candidate(source="lastfm", source_id=case.name, **case.external.model_dump())
        results = [
            candidate(
                source_id=row.id,
                title=row.title,
                artists=[row.artist],
                duration_ms=row.duration_ms,
                explicit=False,
                provenance={"spotify_uri": f"spotify:track:{row.id}"},
            )
            for row in case.results
        ]
        resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FixtureSpotify(results)))
        resolved = await resolver.resolve(external)
        actual_id = resolved.source_id if resolved else None
        if actual_id is not None:
            correct += int(actual_id in case.correct_ids)
            incorrect += int(actual_id not in case.correct_ids)
        elif case.correct_ids:
            missed += 1
        assert actual_id == case.expected_id, case.name

    assert (correct, incorrect, missed, len(cases)) == (5, 0, 0, 10)
