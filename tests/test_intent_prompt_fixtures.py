"""Canonical structured-output fixtures for natural-language intent evaluation."""

import json
from pathlib import Path

import pytest

from tunetrail.domain.models import ListeningIntent
from tunetrail.llm.intent_parser import StructuredIntentParser


class FixtureModel:
    """Return one known structured response for prompt-contract regression tests."""

    def __init__(self, response: object) -> None:
        self.response = response

    async def ainvoke(self, input: object) -> object:
        """Accept the parser prompt and provide the fixture output."""
        assert input
        return self.response


@pytest.mark.parametrize(
    ("listener_request", "expected"),
    [
        (item["request"], item["intent"])
        for item in json.loads(
            (Path(__file__).parent / "fixtures" / "intent_prompts.json").read_text(encoding="utf-8")
        )
    ],
)
async def test_canonical_prompt_fixture_parses_to_the_expected_schema(
    listener_request: str, expected: dict[str, object]
) -> None:
    """Representative direct and adversarial prompts remain strict schema-only inputs."""
    parsed = await StructuredIntentParser(FixtureModel(expected)).parse(listener_request)

    assert parsed == ListeningIntent.model_validate(expected)
