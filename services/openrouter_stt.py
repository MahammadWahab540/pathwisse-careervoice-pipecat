import os
import io
import wave
import time
import base64
from typing import Optional, AsyncGenerator, List
import aiohttp
from loguru import logger

try:
    from pipecat.services.ai_services import SegmentedSTTService as _BaseSTTClass
except ImportError:
    from pipecat.services.ai_services import STTService as _BaseSTTClass

from pipecat.processors.frame_processor import FrameDirection
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    ErrorFrame,
    AudioRawFrame,
    UserAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)


class OpenRouterSTTService(_BaseSTTClass):
    """
    OpenRouter Segmented Speech-to-Text Service for Pipecat.
    Buffers audio frames between VAD speaking start/stop boundaries and transcribes
    complete utterances via OpenRouter's OpenAI-compatible /api/v1/audio/transcriptions endpoint.
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
        self._audio_buffer: bytearray = bytearray()
        self._is_speaking: bool = False

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _pcm_to_wav(self, pcm_bytes: bytes) -> bytes:
        """Encapsulates raw PCM bytes in a standard 16-bit mono WAV container."""
        if pcm_bytes.startswith(b"RIFF"):
            return pcm_bytes
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(pcm_bytes)
        return wav_io.getvalue()

    async def transcribe_audio_bytes(self, audio_bytes: bytes, audio_format: str = "wav") -> Optional[str]:
        """Direct transcription helper using Base64 JSON payload."""
        if not self._api_key or not audio_bytes or len(audio_bytes) < 320:
            return None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://careervoice.pathwisse.com",
            "X-Title": "Pathwisse CareerVoice Agent",
        }

        formatted_audio = self._pcm_to_wav(audio_bytes) if audio_format == "wav" else audio_bytes
        b64_audio = base64.b64encode(formatted_audio).decode("utf-8")

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

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Pipecat STT entrypoint implementing SegmentedSTTService.run_stt for complete utterances."""
        if not audio:
            return

        text = await self.transcribe_audio_bytes(audio, audio_format="wav")
        if text:
            logger.debug(f"OpenRouter STT transcribed utterance: {text}")
            yield TranscriptionFrame(
                text=text,
                user_id="",
                timestamp=str(time.time()),
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Frame-level buffering fallback ensuring complete utterance aggregation."""
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._is_speaking = True
            self._audio_buffer.clear()
        elif isinstance(frame, (AudioRawFrame, UserAudioRawFrame)) and self._is_speaking:
            if hasattr(frame, "audio") and frame.audio:
                self._audio_buffer.extend(frame.audio)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._is_speaking = False
            if len(self._audio_buffer) > 0:
                audio_to_transcribe = bytes(self._audio_buffer)
                self._audio_buffer.clear()
                async for output_frame in self.run_stt(audio_to_transcribe):
                    await self.push_frame(output_frame, direction)
