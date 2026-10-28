"""Tests for LLM provider configuration boundaries."""

import pytest

from tunetrail.config import Settings
from tunetrail.llm.factory import LLMConfigurationError, OpenRouterLLMFactory


def test_factory_requires_openrouter_credentials_and_model() -> None:
    """Natural language remains unavailable until both local settings are present."""
    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY"):
        OpenRouterLLMFactory(Settings(llm_model="openai/gpt-4.1-mini")).create_intent_parser()
    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY"):
        OpenRouterLLMFactory(Settings(llm_model="openai/gpt-4.1-mini")).create_preference_parser()


def test_factory_leaves_direct_provider_adapters_as_clear_extension_points() -> None:
    """Unsupported configured providers fail before any network-capable model is built."""
    settings = Settings(
        llm_provider="anthropic",
        llm_model="claude-test",
        openrouter_api_key="not-used",
    )

    with pytest.raises(LLMConfigurationError, match="not implemented"):
        OpenRouterLLMFactory(settings).create_intent_parser()
