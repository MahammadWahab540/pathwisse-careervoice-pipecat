import os
import sys
import asyncio
import time
from typing import Optional, Dict, Any, List
import aiohttp
from loguru import logger
from dotenv import load_dotenv

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.services.cartesia import CartesiaTTSService
from pipecat.services.google import GoogleLLMService
from pipecat.services.anthropic import AnthropicLLMService
from pipecat.services.openai import OpenAILLMService
from pipecat.processors.aggregators.llm_response import (
    LLMAssistantResponseAggregator,
    LLMUserResponseAggregator,
)

from transports import router, VoiceSessionConfig

load_dotenv()

CAREERVOICE_API_URL = os.getenv("CAREERVOICE_API_URL", "http://localhost:5000").rstrip("/")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()


async def notify_careervoice_signal(
    audit_id: str,
    skill_name: str,
    extracted_level: str,
    confidence_score: int,
    evidence_strength: str,
    raw_answer: str,
):
    """Post extracted skill signals asynchronously back to CareerVoice backend."""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "auditId": audit_id,
                "skillName": skill_name,
                "extractedLevel": extracted_level,
                "confidenceScore": confidence_score,
                "evidenceStrength": evidence_strength,
                "rawAnswerSnippet": raw_answer[:300],
                "source": "pipecat_voice_probe",
            }
            async with session.post(
                f"{CAREERVOICE_API_URL}/api/audit/evidence/signal",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status in (200, 201):
                    logger.info(
                        "voice_evidence_persisted",
                        audit_id=audit_id,
                        skill_name=skill_name,
                        extracted_level=extracted_level,
                        confidence_score=confidence_score,
                    )
                else:
                    logger.warning(
                        "careervoice_signal_failed",
                        audit_id=audit_id,
                        status=resp.status,
                    )
    except Exception as e:
        logger.error(
            "careervoice_signal_post_error",
            audit_id=audit_id,
            error=str(e),
        )


def create_llm_service(provider_preference: str = "gemini"):
    """
    Instantiates the configured LLM provider service.
    Order of selection:
    1. Google Gemini (configured model via GEMINI_MODEL, defaults to gemini-3.6-flash)
    2. Anthropic Claude 3.5 Sonnet
    3. OpenAI GPT-4o-mini
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if gemini_key and provider_preference == "gemini":
        logger.info("Initializing Google Gemini as Primary LLM", model=gemini_model)
        return GoogleLLMService(
            api_key=gemini_key,
            model=gemini_model,
        )
    elif anthropic_key:
        logger.info("Initializing Anthropic Claude as LLM", model="claude-3-5-sonnet-20241022")
        return AnthropicLLMService(
            api_key=anthropic_key,
            model="claude-3-5-sonnet-20241022",
        )
    elif openai_key:
        logger.info("Initializing OpenAI GPT-4o-mini as LLM", model="gpt-4o-mini")
        return OpenAILLMService(
            api_key=openai_key,
            model="gpt-4o-mini",
        )
    elif gemini_key:
        logger.info("Initializing Google Gemini fallback", model=gemini_model)
        return GoogleLLMService(
            api_key=gemini_key,
            model=gemini_model,
        )
    else:
        raise RuntimeError(
            "No usable LLM provider is configured. One of GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY must be set."
        )


async def run_careervoice_agent(session_config: VoiceSessionConfig):
    """
    Executes a real-time conversational CareerVoice audit session using the specified transport (Daily or LiveKit).
    The pipeline logic (VAD -> STT -> LLM -> TTS -> evidence persistence) is provider-agnostic.
    """
    start_time = time.time()
    audit_id = session_config.audit_id
    provider_name = session_config.provider
    target_role = session_config.target_role
    student_name = session_config.student_name

    logger.info(
        "voice_bot_started",
        audit_id=audit_id,
        provider=provider_name,
        target_role=target_role,
        room_name=session_config.room_name,
    )

    try:
        # Resolve transport provider from abstraction factory
        provider = router.get_provider(provider_name)
        transport = provider.create_pipecat_transport(session_config)

        logger.info(
            "voice_bot_joined",
            audit_id=audit_id,
            provider=provider_name,
            room_name=session_config.room_name,
        )

        # STT & TTS Providers
        stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
        tts = CartesiaTTSService(
            api_key=CARTESIA_API_KEY,
            voice_id="79a125e8-cd45-4c13-8a67-188112f4dd22",  # British / Natural conversational persona
        )

        llm = create_llm_service()

        system_instruction = f"""You are Qalam, Pathwisse CareerVoice's lead AI Career Auditor.
You are conducting a strict, encouraging, and evidence-focused 1-on-1 career audit with {student_name} for the role: "{target_role}".

Rules:
1. Speak in short, concise conversational turns (1-3 sentences max).
2. Probe for concrete evidence: ask for specific tools, architecture, algorithms, or code trade-offs.
3. Detect weak evidence: if the candidate says "I know React" or "I studied Python", ask them to explain an actual project or bug they solved.
4. Keep tone warm, professional, and analytical.
"""

        messages = [
            {"role": "system", "content": system_instruction},
            {
                "role": "assistant",
                "content": f"Hello {student_name}! I am Qalam, your AI Career Auditor for the {target_role} track. Let's begin by looking at your latest technical project. What did you build, and what core technologies did you use?",
            },
        ]

        tma_in = LLMUserResponseAggregator(messages)
        tma_out = LLMAssistantResponseAggregator(messages)

        # Provider-independent pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                tma_in,
                llm,
                tts,
                transport.output(),
                tma_out,
            ]
        )

        task = PipelineTask(
            pipeline,
            PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                report_only_initial_ttfb=True,
            ),
        )

        runner = PipelineRunner()
        await runner.run(task)

        duration = round(time.time() - start_time, 2)
        logger.info(
            "voice_session_completed",
            audit_id=audit_id,
            provider=provider_name,
            duration=duration,
        )

    except Exception as e:
        duration = round(time.time() - start_time, 2)
        logger.error(
            "voice_session_failed",
            audit_id=audit_id,
            provider=provider_name,
            duration=duration,
            failureStage="pipeline_execution",
            error=str(e),
        )
        raise


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python bot.py <provider: daily|livekit> <room_url> <token> <audit_id> <target_role> [student_name] [room_name]")
        sys.exit(1)

    prov = sys.argv[1]
    r_url = sys.argv[2]
    tkn = sys.argv[3]
    aud_id = sys.argv[4]
    role = sys.argv[5] if len(sys.argv) > 5 else "Software Engineer"
    name = sys.argv[6] if len(sys.argv) > 6 else "Candidate"
    r_name = sys.argv[7] if len(sys.argv) > 7 else f"careervoice-{aud_id}"

    cfg = VoiceSessionConfig(
        audit_id=aud_id,
        target_role=role,
        student_name=name,
        provider=prov,
        room_url=r_url,
        room_name=r_name,
        token=tkn,
    )

    asyncio.run(run_careervoice_agent(cfg))
