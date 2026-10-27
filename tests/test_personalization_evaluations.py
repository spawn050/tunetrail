"""Fixed Spotify-only personalization regression evaluations."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from tunetrail.domain.models import Candidate, ListeningIntent, ProfileSummary
from tunetrail.recommendation.ranking import (
    build_personalized_queue_preview,
    rank_personalized_candidates,
)


def _object(value: object) -> dict[str, object]:
    """Narrow a JSON fixture object before Pydantic validates it as a candidate."""
    if not isinstance(value, dict):
        raise AssertionError("evaluation candidate must be an object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise AssertionError("evaluation candidate must have string keys")
    return cast(dict[str, object], raw)


def _cases() -> list[dict[str, object]]:
    """Load versioned local-only evaluation cases without service credentials."""
    decoded: object = json.loads(
        (Path(__file__).parent / "fixtures" / "personalization_evaluations.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(decoded, list):
        raise AssertionError("personalization evaluation fixture must be a list of objects")
    return [_object(case) for case in cast(list[object], decoded)]


@pytest.mark.parametrize("case", _cases())
def test_personalization_evaluation_constraints(case: dict[str, object]) -> None:
    """Mood/profile, familiarity, duration, explicit-content, and repetition rules remain stable."""
    intent_data = case["intent"]
    profile_data = case["profile"]
    candidates_data = case["candidates"]
    expected_uris = case["expected_uris"]
    if not isinstance(intent_data, dict) or not isinstance(profile_data, dict):
        raise AssertionError("evaluation intent and profile must be objects")
    if not isinstance(candidates_data, list) or not isinstance(expected_uris, list):
        raise AssertionError("evaluation candidates and expected URIs must be lists")
    candidates = [_object(candidate) for candidate in cast(list[object], candidates_data)]
    expected = cast(list[object], expected_uris)
    if not all(isinstance(uri, str) for uri in expected):
        raise AssertionError("evaluation expected URIs must be strings")
    generated_uris = case.get("generated_uris", [])
    if not isinstance(generated_uris, list):
        raise AssertionError("evaluation generated URIs must be strings")
    generated = cast(list[object], generated_uris)
    if not all(isinstance(uri, str) for uri in generated):
        raise AssertionError("evaluation generated URIs must be strings")
    parsed_intent = ListeningIntent.model_validate(intent_data)
    profile = ProfileSummary.model_validate({**profile_data, "updated_at": datetime.now(UTC)})
    tracks = rank_personalized_candidates(
        [Candidate.model_validate({"source": "spotify", **candidate}) for candidate in candidates],
        parsed_intent,
        profile=profile,
        feedback=[],
        generated_uris=cast(list[str], generated),
    )
    preview = build_personalized_queue_preview(parsed_intent, tracks)

    assert [item.track.spotify_uri for item in preview.items] == cast(list[str], expected)
    if parsed_intent.moods:
        assert "mood/activity metadata is not inferred" in preview.items[0].explanation
