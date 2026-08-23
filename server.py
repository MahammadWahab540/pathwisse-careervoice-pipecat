import os
import secrets
import asyncio
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Response, status
from pydantic import BaseModel, Field
from loguru import logger
from dotenv import load_dotenv

# Load environment variables before importing bot and transports singletons
load_dotenv()

from bot import run_careervoice_agent
from transports import router, SessionProvisionResult, VoiceSessionConfig, sanitize_identifier

app = FastAPI(
    title="Pathwisse CareerVoice Pipecat Voice Server",
    description="Dual Transport (Daily + LiveKit) Real-time Voice Agent for Career Audits",
    version="2.2.0",
)


class VoiceConnectionDetails(BaseModel):
    url: str
    token: str
    roomName: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class StartSessionRequest(BaseModel):
    auditId: str = Field(..., max_length=64, description="Unique CareerVoice audit session UUID")
    targetRole: str = Field(..., max_length=100, description="Target career role being audited")
    studentName: Optional[str] = Field(default="Candidate", max_length=100, description="Candidate first name")
    transport: Optional[str] = Field(default=None, max_length=20, description="Optional transport: 'daily' | 'livekit'")
    # Backwards compatibility fields: existing callers that pre-provisioned Daily rooms
    roomUrl: Optional[str] = Field(default=None, max_length=256, description="Pre-provisioned Daily room URL (deprecated)")
    token: Optional[str] = Field(default=None, max_length=1024, description="Pre-provisioned Daily meeting token (deprecated)")


class StartSessionResponse(BaseModel):
    success: bool
    auditId: str
    provider: str
    roomUrl: str  # Backwards compatibility
    token: str    # Backwards compatibility
    connection: VoiceConnectionDetails


def verify_service_token(authorization: Optional[str] = Header(default=None)):
    """
    Validates service authorization token using constant-time comparison.
    In production (APP_ENV=production), missing CAREERVOICE_SERVICE_TOKEN or missing/invalid
    Bearer authorization will fail-closed and reject the request with HTTP 401.
    """
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    expected_token = os.getenv("CAREERVOICE_SERVICE_TOKEN", "").strip()

    # Fail closed in production if service token is unconfigured
    if app_env == "production" and not expected_token:
        logger.error("auth_failed_production_unconfigured_service_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production environment requires CAREERVOICE_SERVICE_TOKEN to be configured.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not expected_token:
        # Development / local testing without token configured
        logger.warning("auth_skipped_dev_mode_no_service_token")
        return

    if not authorization:
        logger.warning("auth_failed_missing_bearer_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header with Bearer service token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("auth_failed_malformed_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed Authorization header. Format: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided_token = parts[1]
    if not secrets.compare_digest(provided_token, expected_token):
        logger.warning("auth_failed_invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
def health_check():
    """Lightweight liveness probe for orchestrators and load balancers."""
    return {
        "status": "healthy",
        "service": "careervoice-pipecat-voice-agent",
        "transports": ["daily", "livekit"],
    }


@app.get("/ready")
def readiness_check(response: Response):
    """
    Readiness probe reporting transport & AI provider configuration status without exposing secrets.
    Returns HTTP 200 when ready, HTTP 503 when dependencies or production auth are missing.
    """
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    service_token_ok = bool(os.getenv("CAREERVOICE_SERVICE_TOKEN", "").strip())
    auth_ok = service_token_ok if app_env == "production" else True

    deepgram_ok = bool(os.getenv("DEEPGRAM_API_KEY", "").strip())
    cartesia_ok = bool(os.getenv("CARTESIA_API_KEY", "").strip())
    novita_ok = bool(os.getenv("NOVITA_API_KEY", "").strip() or os.getenv("FISH_AUDIO_API_KEY", "").strip())
    tts_ok = cartesia_ok or novita_ok

    gemini_ok = bool(os.getenv("GEMINI_API_KEY", "").strip())
    anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    openai_ok = bool(os.getenv("OPENAI_API_KEY", "").strip())
    llm_ok = gemini_ok or anthropic_ok or openai_ok

    transport_status = router.get_readiness_status()
    has_any_transport = any(t["configured"] for t in transport_status.values())

    is_ready = has_any_transport and llm_ok and deepgram_ok and tts_ok and auth_ok

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if is_ready else "not_ready",
        "environment": app_env,
        "transports": transport_status,
        "defaultTransport": router.default_transport,
        "fallbackTransport": router.fallback_transport,
        "providers": {
            "deepgram": deepgram_ok,
            "cartesia": cartesia_ok,
            "novitaFish": novita_ok,
            "tts": tts_ok,
            "llm": llm_ok,
            "gemini": gemini_ok,
            "anthropic": anthropic_ok,
            "openai": openai_ok,
            "evidenceEvaluator": llm_ok,
            "serviceAuth": service_token_ok,
        },
        "evidenceEvaluatorConfigured": llm_ok,
    }


@app.post("/api/voice/session", response_model=StartSessionResponse, status_code=status.HTTP_200_OK)
async def start_voice_session(
    req: StartSessionRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
):
    # Enforce service-level authentication (fails closed in production)
    verify_service_token(authorization)

    audit_id = sanitize_identifier(req.auditId)
    target_role = req.targetRole.strip()
    student_name = (req.studentName or "Candidate").strip()
    requested_transport = req.transport

    logger.info(
        "voice_session_requested",
        audit_id=audit_id,
        target_role=target_role,
        requested_transport=requested_transport or "auto",
        has_supplied_room=bool(req.roomUrl and req.token),
    )

    try:
        # Compatibility Path: If caller supplied an existing Daily room & token
        if req.roomUrl and req.token:
            room_url = req.roomUrl.strip()
            bot_token = req.token.strip()
            room_name = room_url.rstrip("/").split("/")[-1]

            session_config = VoiceSessionConfig(
                audit_id=audit_id,
                target_role=target_role,
                student_name=student_name,
                provider="daily",
                room_url=room_url,
                room_name=room_name,
                token=bot_token,
                connection_url=room_url,
            )

            background_tasks.add_task(run_careervoice_agent, session_config)

            return StartSessionResponse(
                success=True,
                auditId=audit_id,
                provider="daily",
                roomUrl=room_url,
                token=bot_token,
                connection=VoiceConnectionDetails(
                    url=room_url,
                    token=bot_token,
                    roomName=room_name,
                ),
            )

        # Standard Unified Provisioning Path (Daily / LiveKit with pre-session failover)
        provision_result, provider = await router.provision_session_with_failover(
            audit_id=audit_id,
            target_role=target_role,
            student_name=student_name,
            requested_transport=requested_transport,
        )

        session_config = VoiceSessionConfig(
            audit_id=audit_id,
            target_role=target_role,
            student_name=student_name,
            provider=provision_result.provider,
            room_url=provision_result.room_url,
            room_name=provision_result.room_name,
            token=provision_result.bot_token,
            connection_url=provision_result.connection_url,
        )

        # Spawn Pipecat agent background worker
        background_tasks.add_task(run_careervoice_agent, session_config)

        return StartSessionResponse(
            success=True,
            auditId=audit_id,
            provider=provision_result.provider,
            roomUrl=provision_result.room_url,
            token=provision_result.student_token,
            connection=VoiceConnectionDetails(
                url=provision_result.connection_url,
                token=provision_result.student_token,
                roomName=provision_result.room_name,
                extra=provision_result.extra,
            ),
        )
    except ValueError as ve:
        logger.warning(
            "voice_session_validation_error",
            audit_id=audit_id,
            error=str(ve),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error(
            "voice_session_provisioning_error",
            audit_id=audit_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voice session provisioning failed. Please retry.",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
