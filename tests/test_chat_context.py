"""Offline tests for bounded advisory chat context."""

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tunetrail.api.app import Services, create_app
from tunetrail.api.schemas import ChatInput
from tunetrail.config import Settings
from tunetrail.llm.intent_parser import StructuredPreferenceParser


def test_context_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ChatInput(message="hello", context=["x"] * 9)
    with pytest.raises(ValidationError):
        ChatInput(message="hello", context=["x" * 501])


class Model:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def ainvoke(self, input: object) -> object:
        self.calls.append(input)
        if len(self.calls) == 1:
            return {"clarification": "What mood?"}
        return {"intent": {"moods": ["calm"]}}


@pytest.mark.asyncio
async def test_context_is_advisory_and_sent_only_on_second_pass(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")
    model = Model()
    services.parser = StructuredPreferenceParser(model, max_retries=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        response = await client.post(
            "/api/chat",
            headers={"X-TuneTrail-Token": token},
            json={"message": "calmer", "context": ["music for studying"]},
        )
        assert response.json()["intent"]["moods"] == ["calm"]
        assert "music for studying" not in str(model.calls[0])
        assert "music for studying" in str(model.calls[1])
    await services.http.aclose()


@pytest.mark.asyncio
async def test_context_cannot_trigger_actions_or_preferences(tmp_path: Path) -> None:
    services = Services(Settings(), tmp_path / "db", tmp_path / "auth")

    class UnsafeModel(Model):
        async def ainvoke(self, input: object) -> object:
            self.calls.append(input)
            if len(self.calls) == 1:
                return {"clarification": "Please clarify."}
            return {"spotify_action": {"action": "confirm"}}

    services.parser = StructuredPreferenceParser(UnsafeModel(), max_retries=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://127.0.0.1:8766"
    ) as client:
        token = (await client.get("/api/session")).json()["token"]
        response = await client.post(
            "/api/chat",
            headers={"X-TuneTrail-Token": token},
            json={"message": "maybe", "context": ["yes"]},
        )
        assert response.json()["clarification"] == "Please clarify."
    await services.http.aclose()
