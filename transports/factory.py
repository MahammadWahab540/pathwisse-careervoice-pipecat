import os
from typing import Dict, Optional, Tuple
from loguru import logger

from .base import VoiceTransportProvider, SessionProvisionResult, VoiceSessionConfig
from .daily_transport import DailyVoiceTransportProvider
from .livekit_transport import LiveKitVoiceTransportProvider


class TransportRouter:
    """Manages WebRTC transport provider selection, provisioning, and pre-session failover."""

    def __init__(self):
        self._providers: Dict[str, VoiceTransportProvider] = {
            "daily": DailyVoiceTransportProvider(),
            "livekit": LiveKitVoiceTransportProvider(),
        }

    @property
    def default_transport(self) -> str:
        return os.getenv("VOICE_TRANSPORT_DEFAULT", "daily").strip().lower()

    @property
    def fallback_transport(self) -> str:
        return os.getenv("VOICE_TRANSPORT_FALLBACK", "livekit").strip().lower()

    def get_provider(self, name: str) -> VoiceTransportProvider:
        normalized = name.strip().lower()
        if normalized not in self._providers:
            raise ValueError(f"Unsupported voice transport: '{name}'. Supported transports: {list(self._providers.keys())}")
        return self._providers[normalized]

    def get_readiness_status(self) -> Dict[str, Dict[str, bool]]:
        """Returns configuration status for all registered transports without exposing secrets."""
        return {
            name: {"configured": provider.is_configured()}
            for name, provider in self._providers.items()
        }

    async def provision_session_with_failover(
        self,
        audit_id: str,
        target_role: str,
        student_name: str = "Candidate",
        requested_transport: Optional[str] = None,
    ) -> Tuple[SessionProvisionResult, VoiceTransportProvider]:
        """
        Provisions a voice session according to the deterministic transport policy:
        1. If caller explicitly requested a transport ('daily' or 'livekit'), use STRICT mode.
           Failure on explicit request raises an error immediately (no silent provider switching).
        2. If caller omitted transport (or passed 'auto'), use DEFAULT transport with automatic
           fallback to FALLBACK transport on provisioning errors.
        """
        is_explicit = bool(requested_transport and requested_transport.strip().lower() not in ("auto", "default", ""))
        primary_name = (requested_transport if is_explicit else self.default_transport).strip().lower()
        fallback_name = self.fallback_transport

        if primary_name not in self._providers:
            raise ValueError(f"Unsupported transport requested: '{primary_name}'")

        primary_provider = self._providers[primary_name]

        logger.info(
            "voice_transport_selected",
            audit_id=audit_id,
            requested_transport=requested_transport or "auto",
            selected_primary=primary_name,
            mode="strict" if is_explicit else "failover_enabled",
            target_role=target_role,
        )

        try:
            result = await primary_provider.provision_session(
                audit_id=audit_id,
                target_role=target_role,
                student_name=student_name,
            )
            return result, primary_provider
        except Exception as primary_error:
            logger.warning(
                "voice_transport_primary_failed",
                audit_id=audit_id,
                provider=primary_name,
                error=str(primary_error),
            )

            # In strict mode, do not fail over silently
            if is_explicit:
                raise RuntimeError(
                    f"Explicitly requested transport '{primary_name}' failed to provision: {primary_error}"
                ) from primary_error

            # Automatic failover path for default / auto mode
            if fallback_name and fallback_name != primary_name and fallback_name in self._providers:
                fallback_provider = self._providers[fallback_name]
                if fallback_provider.is_configured():
                    logger.info(
                        "voice_transport_fallback_started",
                        audit_id=audit_id,
                        from_provider=primary_name,
                        to_provider=fallback_name,
                    )
                    try:
                        fallback_result = await fallback_provider.provision_session(
                            audit_id=audit_id,
                            target_role=target_role,
                            student_name=student_name,
                        )
                        logger.info(
                            "voice_transport_fallback_succeeded",
                            audit_id=audit_id,
                            provider=fallback_name,
                        )
                        return fallback_result, fallback_provider
                    except Exception as fallback_error:
                        logger.error(
                            "voice_transport_fallback_failed",
                            audit_id=audit_id,
                            provider=fallback_name,
                            error=str(fallback_error),
                        )
                        raise RuntimeError(
                            f"Both primary transport ('{primary_name}') and fallback ('{fallback_name}') failed to provision session. "
                            f"Primary error: {primary_error}; Fallback error: {fallback_error}"
                        ) from fallback_error

            raise primary_error


# Singleton instance
router = TransportRouter()
