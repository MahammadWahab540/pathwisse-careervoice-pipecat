import os
import sys
import json
import asyncio
import time
from typing import Optional, Dict, Any, List
import aiohttp
from loguru import logger
from dotenv import load_dotenv

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.frames.frames import Frame, LLMMessagesFrame
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
    """Post verified skill evidence signal asynchronously back to CareerVoice backend."""
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


async def evaluate_student_evidence_llm(
    answer_text: str,
    target_role: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluates candidate conversational answer using structured LLM analysis.
    Distinguishes genuine, verifiable engineering evidence from weak claims / technology mentions.
    
    Returns structured dict or None if evaluation fails.
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    prompt = f"""You are an expert technical interviewer and Career Assessment Evaluator for Pathwisse CareerVoice.
Analyze the candidate's spoken response for concrete, verifiable engineering skill evidence for the role: "{target_role}".

CANDIDATE RESPONSE:
\"\"\"{answer_text}\"\"\"

EVALUATION RULES:
1. "I know <tech>", "I studied <tech>", "I worked with <tech>", or merely naming technologies without concrete implementation details is INSUFFICIENT evidence.
2. Vague statements ("I made a website", "I did projects") without specific technical decisions, bugs solved, architecture, or code trade-offs is INSUFFICIENT evidence.
3. Concrete evidence REQUIRES at least one of:
   - What they specifically built and personally implemented
   - Architecture or state-management decisions
   - Concrete bugs, debugging methodology, or performance optimizations
   - Technical trade-offs and code design decisions
   - Measurable engineering outcomes
4. NEVER infer competency from word count or generic keyword frequency.
5. If concrete evidence IS found:
   {{
     "skillName": "Specific Skill Name (e.g. React, PostgreSQL, Docker, Python)",
     "evidenceFound": true,
     "extractedLevel": "Foundational" | "Intermediate" | "Advanced" | "Expert",
     "confidenceScore": <integer 50-100 representing confidence in this evidence assessment>,
     "evidenceStrength": "moderate" | "strong",
     "evidenceSnippet": "concise quote demonstrating the skill",
     "requiresFollowUp": false,
     "followUpQuestion": null
   }}
6. If evidence is weak, generic, technology-name-only, or absent:
   {{
     "skillName": "Mentioned Tech or null",
     "evidenceFound": false,
     "extractedLevel": null,
     "confidenceScore": null,
     "evidenceStrength": "insufficient",
     "evidenceSnippet": null,
     "requiresFollowUp": true,
     "followUpQuestion": "A targeted technical question probing what they personally built or debugged with that skill"
   }}

Return ONLY a valid JSON object matching the schema above."""

    try:
        if gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "response_mime_type": "application/json",
                },
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            raw_content = candidates[0]["content"]["parts"][0]["text"]
                            return json.loads(raw_content)
                    else:
                        logger.warning("gemini_evidence_eval_failed", status=resp.status)

        elif openai_key:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {openai_key}"}
            payload = {
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_content = data["choices"][0]["message"]["content"]
                        return json.loads(raw_content)
    except Exception as e:
        logger.warning("evidence_evaluation_error", error=str(e))
        return None

    return None


class CareerVoiceEvidenceEvaluator(FrameProcessor):
    """
    Pipeline processor that analyzes student turns using LLM structured assessment.
    Only persists signals when concrete evidence is proven (evidenceFound == true).
    Never assigns scores based on word count, keyword mentions, or answer length.
    """
    def __init__(self, audit_id: str, target_role: str, student_name: str = "Candidate"):
        super().__init__()
        self.audit_id = audit_id
        self.target_role = target_role
        self.student_name = student_name
        self.last_follow_up: Optional[str] = None
        self._processed_turns = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMMessagesFrame):
            messages = frame.messages
            if messages and len(messages) > 0:
                last_msg = messages[-1]
                if isinstance(last_msg, dict) and last_msg.get("role") == "user":
                    content = str(last_msg.get("content", "")).strip()
                    await self._evaluate_turn(content, messages)

        await self.push_frame(frame, direction)

    async def _evaluate_turn(self, answer_text: str, conversation_history: List[Dict[str, str]]):
        # Skip trivial single-word acknowledgments ("yes", "ok", "sure", "hello")
        words = answer_text.split()
        if len(words) < 2:
            return

        self._processed_turns += 1

        try:
            assessment = await evaluate_student_evidence_llm(
                answer_text=answer_text,
                target_role=self.target_role,
                conversation_history=conversation_history,
            )

            if not assessment:
                return

            if assessment.get("evidenceFound") is True:
                skill_name = assessment.get("skillName")
                extracted_level = assessment.get("extractedLevel")
                confidence_score = assessment.get("confidenceScore")
                evidence_strength = assessment.get("evidenceStrength") or "moderate"
                evidence_snippet = assessment.get("evidenceSnippet") or answer_text

                if skill_name and extracted_level and confidence_score is not None:
                    asyncio.create_task(
                        notify_careervoice_signal(
                            audit_id=self.audit_id,
                            skill_name=str(skill_name),
                            extracted_level=str(extracted_level),
                            confidence_score=int(confidence_score),
                            evidence_strength=str(evidence_strength),
                            raw_answer=str(evidence_snippet),
                        )
                    )
            else:
                # Weak evidence or claims without substance -> no score persisted
                requires_follow_up = assessment.get("requiresFollowUp")
                follow_up = assessment.get("followUpQuestion")
                if requires_follow_up and follow_up:
                    self.last_follow_up = follow_up
                    logger.info(
                        "evidence_insufficient_follow_up_required",
                        audit_id=self.audit_id,
                        skill=assessment.get("skillName"),
                        follow_up=follow_up,
                    )
        except Exception as e:
            # Evaluator errors must NEVER kill the live voice call
            logger.warning(
                "evidence_evaluator_frame_error",
                audit_id=self.audit_id,
                error=str(e),
            )


def create_llm_service(provider_preference: str = "gemini"):
    """
    Instantiates the configured LLM provider service based on availability.
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
    The pipeline logic (VAD -> STT -> Evidence Evaluator -> LLM -> TTS -> WebRTC) is provider-agnostic.
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
            "voice_transport_initialized",
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

        evidence_evaluator = CareerVoiceEvidenceEvaluator(
            audit_id=audit_id,
            target_role=target_role,
            student_name=student_name,
        )

        # Provider-independent pipeline with integrated real-time evidence evaluation
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                tma_in,
                evidence_evaluator,
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
