import os
import secrets
from typing import Any, Optional
from loguru import logger

from .base import VoiceTransportProvider, SessionProvisionResult, VoiceSessionConfig, sanitize_identifier


def get_websocket_transport_classes():
    """Dynamically resolves FastAPIWebsocketTransport classes across Pipecat versions."""
    try:
        from pipecat.transports.network.fastapi_websocket import (
            FastAPIWebsocketTransport,
            FastAPIWebsocketParams,
        )
        return FastAPIWebsocketTransport, FastAPIWebsocketParams
    except ImportError:
        try:
            from pipecat.transports.services.websocket import (
                FastAPIWebsocketTransport,
                FastAPIWebsocketParams,
            )
            return FastAPIWebsocketTransport, FastAPIWebsocketParams
        except ImportError as e:
            logger.warning(f"FastAPIWebsocketTransport not directly importable: {e}")
            return None, None


class DirectWebSocketVoiceTransportProvider(VoiceTransportProvider):
    """
    Direct WebSocket Voice Transport Provider.
    Enables candidates and clients to connect directly to the Pipecat agent over standard
    WebSocket (ws:// or wss://) without requiring Daily.co, LiveKit, or any third-party WebRTC accounts.
    """

    def __init__(self, ws_base_url: Optional[str] = None):
        self._ws_base_url = (ws_base_url or os.getenv("VOICE_WS_BASE_URL", "")).rstrip("/")

    @property
    def name(self) -> str:
        return "websocket"

    def is_configured(self) -> bool:
        """WebSocket direct transport is always configured and ready with zero external API keys."""
        return True

    async def provision_session(
        self,
        audit_id: str,
        target_role: str,
        student_name: str = "Candidate",
    ) -> SessionProvisionResult:
        sanitized_id = sanitize_identifier(audit_id)
        session_token = secrets.token_urlsafe(32)
        room_name = f"ws-{sanitized_id}"

        base = self._ws_base_url
        if base:
            ws_url = f"{base}/ws/voice/{sanitized_id}?token={session_token}"
        else:
            ws_url = f"/ws/voice/{sanitized_id}?token={session_token}"

        return SessionProvisionResult(
            provider="websocket",
            audit_id=audit_id,
            room_url=ws_url,
            room_name=room_name,
            student_token=session_token,
            bot_token=session_token,
            connection_url=ws_url,
            extra={
                "transport": "websocket",
                "direct_streaming": True,
                "requires_third_party_account": False,
            },
        )


    def create_pipecat_transport(self, session: VoiceSessionConfig) -> Any:
        FastAPIWebsocketTransport, FastAPIWebsocketParams = get_websocket_transport_classes()
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        active_ws = getattr(session, "websocket", None)
        if FastAPIWebsocketTransport and FastAPIWebsocketParams and active_ws:
            return FastAPIWebsocketTransport(
                websocket=active_ws,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    vad_enabled=True,
                    vad_analyzer=SileroVADAnalyzer(),
                    vad_audio_passthrough=True,
                ),
            )

        logger.info("instantiating_direct_websocket_transport", audit_id=session.audit_id)
        if FastAPIWebsocketTransport and FastAPIWebsocketParams:
            return FastAPIWebsocketTransport(
                websocket=active_ws,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    vad_enabled=True,
                    vad_analyzer=SileroVADAnalyzer(),
                ),
            )
        
        class GenericWebsocketTransport:
            def __init__(self, session_cfg):
                self.session_cfg = session_cfg
            def input(self):
                return None
            def output(self):
                return None

        return GenericWebsocketTransport(session)
