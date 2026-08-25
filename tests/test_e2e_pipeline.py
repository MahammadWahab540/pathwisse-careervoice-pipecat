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


# ==============================================================================
# TTS Service Factory Tests
# ==============================================================================
def test_tts_service_cartesia_selection():
    from bot import create_tts_service
    with patch.dict(os.environ, {"CARTESIA_API_KEY": "test-cartesia-key", "NOVITA_API_KEY": ""}):
        with patch("bot.CartesiaTTSService") as mock_cartesia:
            create_tts_service("cartesia")
            mock_cartesia.assert_called_once()


def test_tts_service_novita_fish_selection():
    from bot import create_tts_service
    with patch.dict(os.environ, {"CARTESIA_API_KEY": "", "NOVITA_API_KEY": "test-novita-key", "TTS_PROVIDER": "novita"}):
        with patch("bot.FishAudioTTSService") as mock_fish:
            create_tts_service("novita")
            mock_fish.assert_called_once_with(
                api_key="test-novita-key",
                reference_id=None,
                sample_rate=16000,
            )


def test_tts_service_no_keys_raises_runtime_error():
    from bot import create_tts_service
    with patch.dict(os.environ, {"CARTESIA_API_KEY": "", "NOVITA_API_KEY": "", "FISH_AUDIO_API_KEY": ""}, clear=True):
        with pytest.raises(RuntimeError, match="No usable TTS provider is configured"):
            create_tts_service()


@pytest.mark.asyncio
async def test_fish_audio_tts_service_run_tts_generates_audio_frames():
    from services.novita_fish_tts import FishAudioTTSService
    from pipecat.frames.frames import TTSStartedFrame, TTSStoppedFrame, TTSAudioRawFrame

    service = FishAudioTTSService(api_key="fake-key", sample_rate=16000)

    class MockContent:
        async def iter_chunked(self, chunk_size):
            yield b"\x00\x01\x02\x03" * 100

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.content = MockContent()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp

    with patch("aiohttp.ClientSession.post", return_value=mock_cm):
        frames = []
        async for frame in service.run_tts("Hello candidate!"):
            frames.append(frame)

        assert any(isinstance(f, TTSStartedFrame) for f in frames)
        assert any(isinstance(f, TTSAudioRawFrame) for f in frames)
        assert any(isinstance(f, TTSStoppedFrame) for f in frames)

