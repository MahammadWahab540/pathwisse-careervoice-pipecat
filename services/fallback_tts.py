import asyncio
from typing import List, AsyncGenerator, Optional
from loguru import logger

from pipecat.services.ai_services import TTSService
from pipecat.frames.frames import (
    Frame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSAudioRawFrame,
    ErrorFrame,
)


class FallbackTTSService(TTSService):
    """
    Resilient Multi-Provider Fallback TTS Service for Pipecat.
    Sequentially attempts a prioritized list of TTS services (e.g. OpenRouter -> Cartesia -> Novita).
    If a provider fails or yields an ErrorFrame before emitting audio, it automatically fails over
    to the next available configured provider without crashing the voice pipeline or emitting
    unbalanced lifecycle frames.
    """

    def __init__(
        self,
        providers: List[TTSService],
        sample_rate: int = 24000,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._providers = [p for p in providers if p is not None]
        if not self._providers:
            raise ValueError("FallbackTTSService requires at least one configured TTS provider.")

    @property
    def providers(self) -> List[TTSService]:
        return self._providers

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        if not text or not text.strip():
            return

        last_error: Optional[str] = None

        for idx, provider in enumerate(self._providers):
            provider_name = provider.__class__.__name__
            logger.info(f"FallbackTTSService attempting provider [{idx + 1}/{len(self._providers)}]: {provider_name}")

            emitted_audio = False
            provider_failed = False
            pre_audio_buffer: List[Frame] = []

            try:
                async for frame in provider.run_tts(text):
                    if isinstance(frame, ErrorFrame):
                        logger.warning(
                            f"Provider {provider_name} returned ErrorFrame: {frame.error}. "
                            "Triggering fallback..."
                        )
                        last_error = frame.error
                        provider_failed = True
                        break
                    elif isinstance(frame, TTSAudioRawFrame):
                        if not emitted_audio:
                            # Flush buffered pre-audio lifecycle frames (e.g. TTSStartedFrame)
                            for buf_frame in pre_audio_buffer:
                                yield buf_frame
                            pre_audio_buffer.clear()
                            emitted_audio = True
                        yield frame
                    elif isinstance(frame, (TTSStartedFrame, TTSStoppedFrame)):
                        if not emitted_audio:
                            pre_audio_buffer.append(frame)
                        else:
                            yield frame
                    else:
                        if not emitted_audio:
                            pre_audio_buffer.append(frame)
                        else:
                            yield frame

                if emitted_audio and not provider_failed:
                    logger.debug(f"Provider {provider_name} successfully delivered speech output.")
                    return

            except Exception as e:
                logger.warning(f"Provider {provider_name} encountered exception during TTS streaming: {e}")
                last_error = str(e)
                provider_failed = True

            if emitted_audio:
                # If audio was already partially sent and failed midway, ensure downstream lifecycle is closed
                yield TTSStoppedFrame()
                return

        # If all providers exhausted without success
        logger.error(f"All TTS providers in FallbackTTSService failed. Last error: {last_error}")
        yield ErrorFrame(f"All TTS fallback providers failed. Error: {last_error}")
        yield TTSStoppedFrame()
