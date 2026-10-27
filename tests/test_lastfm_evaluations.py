"""Fixed synthetic comparison of Spotify-only and Last.fm-enriched queues."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from tunetrail.domain.models import Candidate, ListeningIntent, ProfileSummary
from tunetrail.domain.protocols import SpotifyGateway
from tunetrail.recommendation.ranking import rank_personalized_candidates
from tunetrail.recommendation.resolver import SpotifyCandidateResolver


class FixtureSpotify:
    """Serve one manually labelled Spotify result per external search."""

    def __init__(self, result: Candidate) -> None:
        self.result = result

    async def search_tracks(self, query: str, *, limit: int = 10) -> Sequence[Candidate]:
        """Return the fixture result, not a live catalog lookup."""
        return [self.result]


async def test_lastfm_comparison_with_spotify_only_fixture() -> None:
    """Measure diversity and labelled match precision without claiming live effectiveness."""
    fixture_dir = Path(__file__).parent / "fixtures"
    baseline_cases = json.loads((fixture_dir / "personalization_evaluations.json").read_text())
    comparison = json.loads((fixture_dir / "lastfm_evaluations.json").read_text())
    case = baseline_cases[0]
    baseline = [
        Candidate.model_validate({"source": "spotify", **item}) for item in case["candidates"]
    ]
    resolved: list[Candidate] = []
    correct = 0
    for row in comparison:
        suggestion = Candidate.model_validate(
            {
                "source": "lastfm",
                "source_id": row["title"],
                "title": row["title"],
                "artists": [row["artist"]],
                "duration_ms": row["duration_ms"],
                "tags": ["peaceful"],
            }
        )
        spotify = Candidate.model_validate(
            {
                "source": "spotify",
                "source_id": row["spotify_id"],
                "title": row["spotify_title"],
                "artists": [row["spotify_artist"]],
                "duration_ms": row["spotify_duration_ms"],
                "explicit": False,
                "provenance": {"spotify_uri": f"spotify:track:{row['spotify_id']}"},
            }
        )
        resolver = SpotifyCandidateResolver(cast(SpotifyGateway, FixtureSpotify(spotify)))
        result = await resolver.resolve(suggestion)
        assert (result is not None) == row["expected_match"]
        if result is not None:
            resolved.append(result)
            correct += int(row["expected_match"])

    enhanced = [*baseline, *resolved]
    assert len(baseline) == 2 and len(enhanced) == 4
    assert len({item.artists[0] for item in baseline}) == 2
    assert len({item.artists[0] for item in enhanced}) == 4
    assert (correct, len(resolved), len(comparison)) == (2, 2, 3)

    intent = ListeningIntent.model_validate({**case["intent"], "queue_size": 4})
    profile = ProfileSummary.model_validate({**case["profile"], "updated_at": datetime.now(UTC)})
    baseline_tracks = rank_personalized_candidates(
        baseline, intent, profile=profile, feedback=[], generated_uris=[]
    )
    enhanced_tracks = rank_personalized_candidates(
        enhanced, intent, profile=profile, feedback=[], generated_uris=[]
    )
    assert len(baseline_tracks) == 2 and len(enhanced_tracks) == 4
    assert all(track.spotify_uri.startswith("spotify:track:") for track in enhanced_tracks)
