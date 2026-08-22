import os
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from pipecat.frames.frames import LLMMessagesFrame
from pipecat.processors.frame_processor import FrameDirection

from bot import (
    CareerVoiceEvidenceEvaluator,
    evaluate_student_evidence_llm,
    notify_careervoice_signal,
)


@pytest.mark.asyncio
async def test_weak_evidence_i_know_react_produces_no_signal():
    """'I know React.' must return evidenceFound=False and persist NO signal."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_weak_react",
        target_role="Frontend Engineer",
        student_name="Candidate",
    )

    weak_assessment = {
        "skillName": "React",
        "evidenceFound": False,
        "extractedLevel": None,
        "confidenceScore": None,
        "evidenceStrength": "insufficient",
        "evidenceSnippet": None,
        "requiresFollowUp": True,
        "followUpQuestion": "Tell me about a React project where you handled state or debugging.",
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=weak_assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "Tell me about your technical skills."},
                    {"role": "user", "content": "I know React."},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_not_called()
            assert evaluator.last_follow_up == "Tell me about a React project where you handled state or debugging."


@pytest.mark.asyncio
async def test_weak_evidence_i_studied_python_produces_no_signal():
    """'I studied Python.' must produce no persisted signal."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_weak_python",
        target_role="Backend Engineer",
        student_name="Candidate",
    )

    weak_assessment = {
        "skillName": "Python",
        "evidenceFound": False,
        "extractedLevel": None,
        "confidenceScore": None,
        "evidenceStrength": "insufficient",
        "evidenceSnippet": None,
        "requiresFollowUp": True,
        "followUpQuestion": "What backend services or algorithms did you write in Python?",
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=weak_assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "What is your main language?"},
                    {"role": "user", "content": "I studied Python."},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_not_called()


@pytest.mark.asyncio
async def test_long_generic_answer_no_automatic_advanced_score():
    """A long answer filled with generic words must not automatically receive a score."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_generic_long",
        target_role="Software Engineer",
        student_name="Candidate",
    )

    long_generic_text = (
        "I am very passionate about coding and software engineering. I have worked very hard "
        "and studied many topics in computer science over the last several years, always doing my best."
    )

    generic_assessment = {
        "skillName": None,
        "evidenceFound": False,
        "extractedLevel": None,
        "confidenceScore": None,
        "evidenceStrength": "insufficient",
        "evidenceSnippet": None,
        "requiresFollowUp": True,
        "followUpQuestion": "Can you describe a specific software application you personally implemented?",
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=generic_assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "Tell me about your experience."},
                    {"role": "user", "content": long_generic_text},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_technology_keywords_no_automatic_strong_evidence():
    """Listing multiple technology keywords without implementation details is insufficient."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_keyword_soup",
        target_role="Full Stack Developer",
        student_name="Candidate",
    )

    keyword_soup = "I know React, Node.js, Python, AWS, Docker, Kubernetes, PostgreSQL, and GraphQL."

    insufficient_assessment = {
        "skillName": "Full Stack",
        "evidenceFound": False,
        "extractedLevel": None,
        "confidenceScore": None,
        "evidenceStrength": "insufficient",
        "evidenceSnippet": None,
        "requiresFollowUp": True,
        "followUpQuestion": "Pick one of those tools like Docker or PostgreSQL and describe how you integrated it into a project.",
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=insufficient_assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "What is your tech stack?"},
                    {"role": "user", "content": keyword_soup},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_not_called()


@pytest.mark.asyncio
async def test_concrete_react_project_evidence_evaluated_and_persisted():
    """Concrete evidence with architecture & state management decisions is evaluated and persisted."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_concrete_react",
        target_role="Frontend Engineer",
        student_name="Candidate",
    )

    answer = (
        "I built an e-commerce catalog in React using custom hooks and Zustand for client state. "
        "To handle thousands of filtered items smoothly, I virtualized the list with react-window "
        "and memoized filter computations with useMemo."
    )

    concrete_assessment = {
        "skillName": "React",
        "evidenceFound": True,
        "extractedLevel": "Advanced",
        "confidenceScore": 88,
        "evidenceStrength": "strong",
        "evidenceSnippet": "Built e-commerce catalog in React using custom hooks, Zustand, and list virtualization with react-window.",
        "requiresFollowUp": False,
        "followUpQuestion": None,
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=concrete_assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "What did you build with React?"},
                    {"role": "user", "content": answer},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_called_once()
            kwargs = mock_signal.call_args[1]
            assert kwargs["audit_id"] == "audit_concrete_react"
            assert kwargs["skill_name"] == "React"
            assert kwargs["extracted_level"] == "Advanced"
            assert kwargs["confidence_score"] == 88
            assert kwargs["evidence_strength"] == "strong"


@pytest.mark.asyncio
async def test_concrete_debugging_example_evaluated_and_persisted():
    """Concrete debugging methodology and root-cause resolution is evaluated and persisted."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_debugging_01",
        target_role="Backend Engineer",
        student_name="Candidate",
    )

    answer = (
        "We had a connection pool exhaustion bug in PostgreSQL under heavy traffic. "
        "I analyzed query execution times with pg_stat_activity, discovered unindexed foreign key queries, "
        "added composite indexes, and implemented connection pooling with PgBouncer, cutting latency by 60%."
    )

    debugging_assessment = {
        "skillName": "PostgreSQL Performance Tuning",
        "evidenceFound": True,
        "extractedLevel": "Advanced",
        "confidenceScore": 92,
        "evidenceStrength": "strong",
        "evidenceSnippet": "Diagnosed connection pool exhaustion using pg_stat_activity, added composite indexes and PgBouncer connection pooling.",
        "requiresFollowUp": False,
        "followUpQuestion": None,
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=debugging_assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "Tell me about a complex bug you solved."},
                    {"role": "user", "content": answer},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_called_once()
            kwargs = mock_signal.call_args[1]
            assert kwargs["audit_id"] == "audit_debugging_01"
            assert kwargs["skill_name"] == "PostgreSQL Performance Tuning"
            assert kwargs["confidence_score"] == 92


@pytest.mark.asyncio
async def test_evaluator_malformed_json_continues_safely():
    """Malformed LLM JSON does not raise exceptions or crash the pipeline."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_malformed_json",
        target_role="DevOps Engineer",
        student_name="Candidate",
    )

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=None):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "How do you manage CI/CD?"},
                    {"role": "user", "content": "I built GitHub Actions workflows deploying to AWS ECS with blue-green deployments."},
                ]
            )
            # Must complete without exception
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)
            mock_signal.assert_not_called()


@pytest.mark.asyncio
async def test_evaluator_api_failure_continues_safely():
    """LLM API failure (e.g. timeout) does not crash the voice session."""
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id="audit_api_fail",
        target_role="Backend Engineer",
        student_name="Candidate",
    )

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, side_effect=RuntimeError("LLM API Timeout")):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "What did you build?"},
                    {"role": "user", "content": "I built a distributed cache in Redis."},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)
            mock_signal.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_failure_voice_session_continues():
    """Webhook HTTP 500 failure does not raise an exception to the caller."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_post.return_value.__aenter__.return_value = mock_resp

        # Calling notify_careervoice_signal must complete safely without raising
        await notify_careervoice_signal(
            audit_id="audit_webhook_fail",
            skill_name="Docker",
            extracted_level="Intermediate",
            confidence_score=75,
            evidence_strength="moderate",
            raw_answer="Candidate wrote Dockerfile multi-stage builds.",
        )


@pytest.mark.asyncio
async def test_audit_id_remains_unchanged_throughout_evaluation():
    """Audit ID passed to evaluator is preserved exactly across signal notifications."""
    test_audit_id = "audit-unique-uuid-9988-7766"
    evaluator = CareerVoiceEvidenceEvaluator(
        audit_id=test_audit_id,
        target_role="Cloud Architect",
        student_name="Candidate",
    )

    assessment = {
        "skillName": "AWS Lambda",
        "evidenceFound": True,
        "extractedLevel": "Advanced",
        "confidenceScore": 85,
        "evidenceStrength": "strong",
        "evidenceSnippet": "Built serverless event pipelines using AWS Lambda and SQS.",
        "requiresFollowUp": False,
        "followUpQuestion": None,
    }

    with patch("bot.evaluate_student_evidence_llm", new_callable=AsyncMock, return_value=assessment):
        with patch("bot.notify_careervoice_signal", new_callable=AsyncMock) as mock_signal:
            frame = LLMMessagesFrame(
                messages=[
                    {"role": "assistant", "content": "What AWS services did you use?"},
                    {"role": "user", "content": "I built serverless event pipelines with AWS Lambda and SQS."},
                ]
            )
            await evaluator.process_frame(frame, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)

            mock_signal.assert_called_once()
            assert mock_signal.call_args[1]["audit_id"] == test_audit_id
