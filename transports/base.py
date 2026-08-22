import abc
import re
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


def sanitize_identifier(value: str, max_len: int = 64) -> str:
    """Sanitizes an identifier (e.g. auditId) to be safe for WebRTC room and participant names."""
    if not value or not isinstance(value, str):
        return "session"
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", value.strip())
    truncated = sanitized[:max_len].strip("-")
    return truncated if truncated else "session"


class SessionProvisionResult(BaseModel):
    """Normalized output from provisioning a WebRTC voice room and tokens."""
    provider: str
    audit_id: str
    room_url: str
    room_name: str
    student_token: str
    bot_token: str
    connection_url: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class VoiceSessionConfig(BaseModel):
    """Configuration passed into the Pipecat agent worker."""
    audit_id: str
    target_role: str
    student_name: str = "Candidate"
    provider: str
    room_url: str
    room_name: str
    token: str  # Bot token
    connection_url: Optional[str] = None


class VoiceTransportProvider(abc.ABC):
    """Abstract interface for WebRTC voice transport providers (Daily, LiveKit, etc.)."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'daily', 'livekit')."""
        pass

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Checks if the provider has all required credentials/endpoints configured."""
        pass

    @abc.abstractmethod
    async def provision_session(
        self,
        audit_id: str,
        target_role: str,
        student_name: str = "Candidate",
    ) -> SessionProvisionResult:
        """Provisions room and participant tokens for student and bot."""
        pass

    @abc.abstractmethod
    def create_pipecat_transport(self, session: VoiceSessionConfig) -> Any:
        """Constructs the appropriate Pipecat Transport instance for this provider."""
        pass
