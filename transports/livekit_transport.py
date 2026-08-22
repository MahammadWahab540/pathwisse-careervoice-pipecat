import os
from datetime import timedelta
from typing import Any, Tuple
from loguru import logger

from .base import VoiceTransportProvider, SessionProvisionResult, VoiceSessionConfig, sanitize_identifier


def get_livekit_transport_classes():
    """Dynamically resolves LiveKit transport classes across Pipecat version changes."""
    try:
        from pipecat.transports.services.livekit import LiveKitParams, LiveKitTransport
        return LiveKitTransport, LiveKitParams
    except ImportError:
        try:
            from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
            return LiveKitTransport, LiveKitParams
        except ImportError as e:
            logger.error(f"Failed to import LiveKit transport from Pipecat: {e}")
            raise RuntimeError("LiveKit transport is not available in installed Pipecat package.") from e


def generate_livekit_token(
    api_key: str,
    api_secret: str,
    identity: str,
    name: str,
    room_name: str,
    ttl_seconds: int = 3600,
) -> str:
    """Generates a signed LiveKit AccessToken with room join permissions."""
    try:
        from livekit import api
        token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(identity)
            .with_name(name)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_ttl(timedelta(seconds=ttl_seconds))
        )
        return token.to_jwt()
    except ImportError:
        # Fallback if livekit-api is imported directly
        from livekit.api import AccessToken, VideoGrants
        token = (
            AccessToken(api_key, api_secret)
            .with_identity(identity)
            .with_name(name)
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_ttl(timedelta(seconds=ttl_seconds))
        )
        return token.to_jwt()


class LiveKitVoiceTransportProvider(VoiceTransportProvider):
    """LiveKit WebRTC Transport Provider implementation."""

    def __init__(
        self,
        url: str = "",
        api_key: str = "",
        api_secret: str = "",
    ):
        self._url = url or os.getenv("LIVEKIT_URL", "").strip()
        self._api_key = api_key or os.getenv("LIVEKIT_API_KEY", "").strip()
        self._api_secret = api_secret or os.getenv("LIVEKIT_API_SECRET", "").strip()

    @property
    def name(self) -> str:
        return "livekit"

    def is_configured(self) -> bool:
        return bool(self._url and self._api_key and self._api_secret)

    async def provision_session(
        self,
        audit_id: str,
        target_role: str,
        student_name: str = "Candidate",
    ) -> SessionProvisionResult:
        if not self.is_configured():
            raise ValueError(
                "LiveKit provider is not configured. LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET are required."
            )

        sanitized_id = sanitize_identifier(audit_id)
        room_name = f"careervoice-{sanitized_id}"
        student_identity = f"student-{sanitized_id}"
        bot_identity = f"qalam-{sanitized_id}"

        # Generate scoped participant JWT for student
        student_token = generate_livekit_token(
            api_key=self._api_key,
            api_secret=self._api_secret,
            identity=student_identity,
            name=student_name,
            room_name=room_name,
        )

        # Generate scoped participant JWT for Qalam AI agent bot
        bot_token = generate_livekit_token(
            api_key=self._api_key,
            api_secret=self._api_secret,
            identity=bot_identity,
            name="Qalam - AI Career Auditor",
            room_name=room_name,
        )

        return SessionProvisionResult(
            provider="livekit",
            audit_id=audit_id,
            room_url=self._url,
            room_name=room_name,
            student_token=student_token,
            bot_token=bot_token,
            connection_url=self._url,
            extra={
                "studentIdentity": student_identity,
                "botIdentity": bot_identity,
            },
        )

    def create_pipecat_transport(self, session: VoiceSessionConfig) -> Any:
        LiveKitTransport, LiveKitParams = get_livekit_transport_classes()
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        # In current Pipecat LiveKitTransport API: LiveKitTransport(url, token, room_name, params=...)
        return LiveKitTransport(
            session.connection_url or session.room_url,
            session.token,
            session.room_name,
            LiveKitParams(
                audio_out_enabled=True,
                audio_in_enabled=True,
                camera_out_enabled=False,
                camera_in_enabled=False,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
            ),
        )
