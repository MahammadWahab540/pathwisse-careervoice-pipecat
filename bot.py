import os
import sys
import json
import asyncio
from typing import Optional, Dict, Any, List
import aiohttp
from loguru import logger
from dotenv import load_dotenv

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.services.cartesia import CartesiaTTSService
from pipecat.services.google import GoogleLLMService
from pipecat.services.anthropic import AnthropicLLMService
from pipecat.services.openai import OpenAILLMService
from pipecat.processors.aggregators.llm_response import (
    LLMAssistantResponseAggregator,
    LLMUserResponseAggregator,
)

load_dotenv()

CAREERVOICE_API_URL = os.getenv("CAREERVOICE_API_URL", "http://localhost:5000")
DAILY_API_KEY = os.getenv("DAILY_API_KEY", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


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
                f"{CAREERVOICE_API_URL}/api/audit/evidence/signal", json=payload
            ) as resp:
                if resp.status != 201 and resp.status != 200:
                    logger.warning(f"CareerVoice signal notification returned status {resp.status}")
    except Exception as e:
        logger.error(f"Failed to post signal to CareerVoice backend: {e}")


def create_llm_service(provider_preference: str = "gemini"):
    """
    Instantiates the appropriate LLM service with fallback capability.
    Order of preference:
    1. Google Gemini 1.5 Flash
    2. Anthropic Claude 3.5 Sonnet
    3. OpenAI GPT-4o-mini
    """
    if GEMINI_API_KEY and provider_preference == "gemini":
        logger.info("Initializing Google Gemini 1.5 Flash as Primary LLM")
        return GoogleLLMService(
            api_key=GEMINI_API_KEY,
            model="models/gemini-1.5-flash-latest",
        )
    elif ANTHROPIC_API_KEY:
        logger.info("Initializing Anthropic Claude 3.5 Sonnet as LLM")
        return AnthropicLLMService(
            api_key=ANTHROPIC_API_KEY,
            model="claude-3-5-sonnet-20241022",
        )
    elif OPENAI_API_KEY:
        logger.info("Initializing OpenAI GPT-4o-mini as LLM")
        return OpenAILLMService(
            api_key=OPENAI_API_KEY,
            model="gpt-4o-mini",
        )
    elif GEMINI_API_KEY:
        return GoogleLLMService(
            api_key=GEMINI_API_KEY,
            model="models/gemini-1.5-flash-latest",
        )
    else:
        raise ValueError("No LLM API keys configured (GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY required).")


async def run_careervoice_agent(
    room_url: str,
    token: str,
    audit_id: str,
    target_role: str,
    student_name: str = "Candidate",
):
    """Executes a real-time conversational CareerVoice audit session using Pipecat."""
    logger.info(f"Starting CareerVoice Pipecat Agent for audit: {audit_id} in room: {room_url}")

    transport = DailyTransport(
        room_url,
        token,
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


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python bot.py <room_url> <token> <audit_id> <target_role> [student_name]")
        sys.exit(1)

    r_url = sys.argv[1]
    tkn = sys.argv[2]
    aud_id = sys.argv[3]
    role = sys.argv[4]
    name = sys.argv[5] if len(sys.argv) > 5 else "Candidate"

    asyncio.run(run_careervoice_agent(r_url, tkn, aud_id, role, name))
