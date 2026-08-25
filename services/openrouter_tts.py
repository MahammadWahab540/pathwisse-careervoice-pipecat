import os
from typing import Optional, AsyncGenerator
import aiohttp
from loguru import logger

from pipecat.services.ai_services import TTSService
from pipecat.frames.frames import (
    Frame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSAudioRawFrame,
    ErrorFrame,
)


class OpenRouterTTSService(TTSService):
    """
    OpenRouter Text-to-Speech Service implementation for Pipecat.
    Calls OpenRouter's OpenAI-compatible /api/v1/audio/speech endpoint to stream audio.
    Supports PCM (uncompressed low-latency) and MP3 formats.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1/audio/speech",
        model: str = "fish-audio/s2.1-pro",
        voice: Optional[str] = None,
        response_format: str = "pcm",
        sample_rate: int = 44100,
        speed: float = 1.0,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
        self._base_url = (base_url or os.getenv("OPENROUTER_TTS_BASE_URL", "https://openrouter.ai/api/v1/audio/speech")).rstrip("/")
        self._model = model or os.getenv("OPENROUTER_TTS_MODEL", "fish-audio/s2.1-pro")
        self._voice = voice or os.getenv("OPENROUTER_TTS_VOICE", "")
        self._response_format = response_format or os.getenv("OPENROUTER_TTS_FORMAT", "pcm")
        self._sample_rate = sample_rate
        self._speed = speed

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        if not text or not text.strip():
            return

        if not self._api_key:
            logger.error("OpenRouterTTSService called without OPENROUTER_API_KEY configured.")
            yield ErrorFrame("OpenRouter TTS error: API key is not configured.")
            return

        logger.debug(
            f"OpenRouterTTSService generating speech for text: {text[:60]}... "
            f"[model={self._model}, voice={self._voice}, format={self._response_format}]"
        )
        yield TTSStartedFrame()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://careervoice.pathwisse.com",
            "X-Title": "Pathwisse CareerVoice Agent",
        }

        payload = {
            "model": self._model,
            "input": text,
            "voice": self._voice,
            "response_format": self._response_format,
            "speed": self._speed,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._base_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.error(f"OpenRouter TTS API error (status {resp.status}): {err_text}")
                        yield ErrorFrame(f"OpenRouter TTS API error ({resp.status}): {err_text}")
                        yield TTSStoppedFrame()
                        return

                    # Stream audio chunks in 1024-byte frames for low-latency playback
                    async for chunk in resp.content.iter_chunked(1024):
                        if chunk:
                            yield TTSAudioRawFrame(
                                audio=chunk,
                                sample_rate=self._sample_rate,
                                num_channels=1,
                            )

            yield TTSStoppedFrame()
        except Exception as e:
            logger.error(f"OpenRouter TTS streaming exception: {e}")
            yield ErrorFrame(f"OpenRouter TTS streaming failed: {e}")
            yield TTSStoppedFrame()
