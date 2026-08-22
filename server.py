import os
import asyncio
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, Field
from loguru import logger
from dotenv import load_dotenv

from bot import run_careervoice_agent
from transports import router, SessionProvisionResult, VoiceSessionConfig

load_dotenv()

app = FastAPI(
    title="Pathwisse CareerVoice Pipecat Voice Server",
    description="Dual Transport (Daily + LiveKit) Real-time Voice Agent for Career Audits",
    version="2.0.0",
)


class VoiceConnectionDetails(BaseModel):
    url: str
    token: str
    roomName: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class StartSessionRequest(BaseModel):
    auditId: str
    targetRole: str
    studentName: Optional[str] = "Candidate"
    transport: Optional[str] = None  # "daily" | "livekit" | None (uses default)


class StartSessionResponse(BaseModel):
    success: bool
    auditId: str
    provider: str
    roomUrl: str  # Backwards compatibility
    token: str    # Backwards compatibility
    connection: VoiceConnectionDetails


@app.get("/health")
def health_check():
    """Liveness probe for load balancers / orchestrators."""
    return {
        "status": "healthy",
        "service": "careervoice-pipecat-voice-agent",
        "transports": ["daily", "livekit"],
    }


@app.get("/ready")
def readiness_check():
    """Readiness probe reporting transport & model configuration status without exposing secrets."""
    deepgram_ok = bool(os.getenv("DEEPGRAM_API_KEY", "").strip())
    cartesia_ok = bool(os.getenv("CARTESIA_API_KEY", "").strip())
    gemini_ok = bool(os.getenv("GEMINI_API_KEY", "").strip())
    anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    openai_ok = bool(os.getenv("OPENAI_API_KEY", "").strip())
    llm_ok = gemini_ok or anthropic_ok or openai_ok

    transport_status = router.get_readiness_status()
    has_any_transport = any(t["configured"] for t in transport_status.values())

    is_ready = has_any_transport and llm_ok and deepgram_ok and cartesia_ok

    response_body = {
        "status": "ready" if is_ready else "not_ready",
        "transports": transport_status,
        "defaultTransport": router.default_transport,
        "fallbackTransport": router.fallback_transport,
        "providers": {
            "deepgram": deepgram_ok,
            "cartesia": cartesia_ok,
            "llm": llm_ok,
            "gemini": gemini_ok,
            "anthropic": anthropic_ok,
            "openai": openai_ok,
        },
    }

    if not is_ready:
        return response_body

    return response_body


@app.post("/api/voice/session", response_model=StartSessionResponse, status_code=status.HTTP_200_OK)
async def start_voice_session(req: StartSessionRequest, background_tasks: BackgroundTasks):
    audit_id = req.auditId
    target_role = req.targetRole
    student_name = req.studentName or "Candidate"
    requested_transport = req.transport

    logger.info(
        "voice_session_requested",
        audit_id=audit_id,
        target_role=target_role,
        requested_transport=requested_transport or "default",
    )

    try:
        # Provision session with automatic pre-session failover across Daily and LiveKit
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

        # Spawn the Pipecat agent background worker task
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
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(
            "voice_session_provisioning_error",
            audit_id=audit_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Voice session provisioning failed: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
