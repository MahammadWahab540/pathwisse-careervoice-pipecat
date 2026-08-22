import os
import time
from typing import Any, Tuple, Optional
import aiohttp
from loguru import logger

from .base import VoiceTransportProvider, SessionProvisionResult, VoiceSessionConfig, sanitize_identifier

DAILY_API_URL = "https://api.daily.co/v1"


def get_daily_transport_classes():
    """Dynamically resolves Daily transport classes across Pipecat version changes."""
    try:
        from pipecat.transports.services.daily import DailyParams, DailyTransport
        return DailyTransport, DailyParams
    except ImportError:
        try:
            from pipecat.transports.daily.transport import DailyTransport, DailyParams
            return DailyTransport, DailyParams
        except ImportError as e:
            logger.error(f"Failed to import Daily transport from Pipecat: {e}")
            raise RuntimeError("Daily transport is not available in installed Pipecat package.") from e


class DailyVoiceTransportProvider(VoiceTransportProvider):
    """Daily.co WebRTC Transport Provider implementation with dynamic credential evaluation."""

    def __init__(self, api_key: str = "", api_url: str = DAILY_API_URL):
        self._explicit_api_key = api_key
        self._api_url = api_url.rstrip("/")

    @property
    def api_key(self) -> str:
        return self._explicit_api_key if self._explicit_api_key else os.getenv("DAILY_API_KEY", "").strip()

    @property
    def name(self) -> str:
        return "daily"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _create_daily_room(self, session_name: str) -> Tuple[str, str]:
        """Creates a temporary WebRTC room with 1-hour expiry using Unix epoch timestamp."""
        key = self.api_key
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        # Standard Unix epoch timestamp for room expiration
        exp_timestamp = int(time.time()) + 3600

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{self._api_url}/rooms",
                headers=headers,
                json={
                    "name": f"careervoice-{session_name}-{int(time.time())}",
                    "properties": {
                        "exp": exp_timestamp,
                        "enable_chat": False,
                        "enable_screenshare": False,
                    },
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Daily room creation failed (status {resp.status}): {text}")
                data = await resp.json()
                return data["url"], data["name"]

    async def _create_meeting_token(self, room_name: str, is_owner: bool, user_name: str) -> str:
        """Generates a scoped meeting token for a participant."""
        key = self.api_key
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        exp_timestamp = int(time.time()) + 3600

        payload = {
            "properties": {
                "room_name": room_name,
                "is_owner": is_owner,
                "user_name": user_name,
                "exp": exp_timestamp,
            }
        }

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{self._api_url}/meeting-tokens",
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Daily token creation failed (status {resp.status}): {text}")
                data = await resp.json()
                return data["token"]

    async def provision_session(
        self,
        audit_id: str,
        target_role: str,
        student_name: str = "Candidate",
    ) -> SessionProvisionResult:
        if not self.is_configured():
            raise ValueError("Daily provider is not configured. DAILY_API_KEY is required.")

        sanitized_id = sanitize_identifier(audit_id)
        room_url, room_name = await self._create_daily_room(sanitized_id)

        student_token = await self._create_meeting_token(
            room_name=room_name,
            is_owner=False,
            user_name=student_name,
        )

        bot_token = await self._create_meeting_token(
            room_name=room_name,
            is_owner=True,
            user_name="Qalam - AI Career Auditor",
        )

        return SessionProvisionResult(
            provider="daily",
            audit_id=audit_id,
            room_url=room_url,
            room_name=room_name,
            student_token=student_token,
            bot_token=bot_token,
            connection_url=room_url,
        )

    def create_pipecat_transport(self, session: VoiceSessionConfig) -> Any:
        DailyTransport, DailyParams = get_daily_transport_classes()
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        return DailyTransport(
            session.room_url,
            session.token,
            "Qalam - AI Career Auditor",
            DailyParams(
                audio_out_enabled=True,
                audio_in_enabled=True,
                camera_out_enabled=False,
                camera_in_enabled=False,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                transcription_enabled=False,
            ),
        )
