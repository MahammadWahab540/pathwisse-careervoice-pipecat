import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from transports.base import VoiceSessionConfig
from bot import create_llm_service


def test_llm_service_gemini_selection():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-3.6-flash"}):
        with patch("bot.GoogleLLMService") as mock_google:
            create_llm_service("gemini")
            mock_google.assert_called_once_with(api_key="test-key", model="gemini-3.6-flash")


def test_llm_service_anthropic_selection():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "ANTHROPIC_API_KEY": "test-claude-key"}):
        with patch("bot.AnthropicLLMService") as mock_claude:
            create_llm_service("gemini")
            mock_claude.assert_called_once_with(api_key="test-claude-key", model="claude-3-5-sonnet-20241022")


def test_llm_service_openai_selection():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "test-openai-key"}):
        with patch("bot.OpenAILLMService") as mock_openai:
            create_llm_service("gemini")
            mock_openai.assert_called_once_with(api_key="test-openai-key", model="gpt-4o-mini")


def test_llm_service_no_keys_raises_runtime_error():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""}, clear=True):
        with pytest.raises(RuntimeError, match="No usable LLM provider is configured"):
            create_llm_service("gemini")
