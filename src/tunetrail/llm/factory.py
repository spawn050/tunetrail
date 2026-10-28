"""Provider-neutral construction of LangChain intent parsers."""

from typing import Protocol, cast

from langchain_openrouter import ChatOpenRouter
from pydantic import SecretStr

from tunetrail.config import Settings
from tunetrail.domain.models import ChatInterpretation, ListeningIntent
from tunetrail.domain.protocols import IntentParser
from tunetrail.llm.intent_parser import (
    StructuredChatModel,
    StructuredIntentParser,
    StructuredPreferenceParser,
)


class ChatModel(Protocol):
    """The LangChain model capability required for strict structured output."""

    def with_structured_output(self, schema: type[ListeningIntent]) -> object: ...


class LLMConfigurationError(ValueError):
    """Raised when an LLM provider cannot be safely configured."""


class OpenRouterLLMFactory:
    """Build the initial OpenRouter-backed parser behind the domain protocol."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_intent_parser(self) -> IntentParser:
        """Create a schema-bound parser without exposing LLM access to the graph."""
        model = self._create_model()
        structured = cast(
            object,
            model.with_structured_output(ListeningIntent),  # type: ignore[reportUnknownMemberType]
        )
        return StructuredIntentParser(
            cast(StructuredChatModel, structured), max_retries=self._settings.llm_max_retries
        )

    def create_preference_parser(self) -> StructuredPreferenceParser:
        """Build the structured parser for the independent chat profile."""
        model = self._create_model()
        structured = cast(
            object,
            model.with_structured_output(ChatInterpretation),  # type: ignore[reportUnknownMemberType]
        )
        return StructuredPreferenceParser(
            cast(StructuredChatModel, structured), max_retries=self._settings.llm_max_retries
        )

    def _create_model(self) -> ChatOpenRouter:
        """Validate settings before constructing a network-capable model."""
        if self._settings.llm_provider != "openrouter":
            raise LLMConfigurationError(
                f"LLM provider '{self._settings.llm_provider}' is not implemented yet. "
                "Use OpenRouter for now."
            )
        if self._settings.openrouter_api_key is None:
            raise LLMConfigurationError(
                "Set TUNETRAIL_OPENROUTER_API_KEY in .env for natural language."
            )
        if self._settings.llm_model is None:
            raise LLMConfigurationError("Set TUNETRAIL_LLM_MODEL in .env for natural language.")
        return ChatOpenRouter(
            api_key=SecretStr(self._settings.openrouter_api_key),
            model=self._settings.llm_model,
            temperature=self._settings.llm_temperature,
            max_retries=self._settings.llm_max_retries,
            openrouter_provider={
                "data_collection": "allow" if self._settings.llm_allow_data_collection else "deny"
            },
        )
