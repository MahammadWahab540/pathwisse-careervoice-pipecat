import os
import base64
from typing import Optional, AsyncGenerator
import aiohttp
from loguru import logger

from pipecat.services.ai_services import STTService
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    ErrorFrame,
    AudioRawFrame,
)


class OpenRouterSTTService(STTService):
    """
    OpenRouter Speech-to-Text Service implementation for Pipecat.
    Transcribes audio using OpenRouter's OpenAI-compatible /api/v1/audio/transcriptions endpoint.
    Supports Whisper models (e.g. openai/whisper-large-v3, openai/whisper-1).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1/audio/transcriptions",
        model: str = "openai/whisper-large-v3",
        language: Optional[str] = "en",
        sample_rate: int = 16000,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
        self._base_url = (base_url or os.getenv("OPENROUTER_STT_BASE_URL", "https://openrouter.ai/api/v1/audio/transcriptions")).rstrip("/")
        self._model = model or os.getenv("OPENROUTER_STT_MODEL", "openai/whisper-large-v3")
        self._language = language
        self._sample_rate = sample_rate

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def transcribe_audio_bytes(self, audio_bytes: bytes, audio_format: str = "wav") -> Optional[str]:
        """Direct transcription helper using Base64 JSON payload."""
        if not self._api_key or not audio_bytes:
            return None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://careervoice.pathwisse.com",
            "X-Title": "Pathwisse CareerVoice Agent",
        }

        b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
        payload = {
            "model": self._model,
            "input_audio": {
                "data": b64_audio,
                "format": audio_format,
            },
        }
        if self._language:
            payload["language"] = self._language

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._base_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("text", "").strip()
                    else:
                        err_text = await resp.text()
                        logger.warning(f"OpenRouter STT API error (status {resp.status}): {err_text}")
                        return None
        except Exception as e:
            logger.error(f"OpenRouter STT request failed: {e}")
            return None
