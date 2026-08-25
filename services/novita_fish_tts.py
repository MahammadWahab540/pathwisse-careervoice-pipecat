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


class FishAudioTTSService(TTSService):
    """
    Novita AI / Fish Audio (S1) Text-to-Speech Service implementation for Pipecat.
    Converts text to speech and yields streaming raw PCM audio frames.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.novita.ai/v3/tts",
        model: str = "s1",
        reference_id: Optional[str] = None,
        sample_rate: int = 16000,
        temperature: float = 0.9,
        top_p: float = 0.9,
        chunk_length: int = 200,
        latency: str = "balanced",
        normalize: bool = True,
        speed: float = 1.0,
        volume: float = 0.0,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._reference_id = reference_id
        self._sample_rate = sample_rate
        self._temperature = temperature
        self._top_p = top_p
        self._chunk_length = chunk_length
        self._latency = latency
        self._normalize = normalize
        self._speed = speed
        self._volume = volume

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        if not text or not text.strip():
            return

        logger.debug(f"FishAudioTTSService generating speech for text: {text[:60]}...")
        yield TTSStartedFrame()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "model": self._model,
        }

        payload = {
            "text": text,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "chunk_length": self._chunk_length,
            "normalize": self._normalize,
            "format": "pcm",
            "sample_rate": self._sample_rate,
            "latency": self._latency,
            "prosody": {"speed": self._speed, "volume": self._volume},
        }
        if self._reference_id:
            payload["reference_id"] = self._reference_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._base_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.error(f"Fish Audio TTS API error (status {resp.status}): {err_text}")
                        yield ErrorFrame(f"Fish Audio TTS error: {err_text}")
                        yield TTSStoppedFrame()
                        return

                    # Stream PCM chunks in 1024-byte frames
                    async for chunk in resp.content.iter_chunked(1024):
                        if chunk:
                            yield TTSAudioRawFrame(
                                audio=chunk,
                                sample_rate=self._sample_rate,
                                num_channels=1,
                            )

            yield TTSStoppedFrame()
        except Exception as e:
            logger.error(f"Fish Audio TTS streaming exception: {e}")
            yield ErrorFrame(f"Fish Audio TTS streaming failed: {e}")
            yield TTSStoppedFrame()
