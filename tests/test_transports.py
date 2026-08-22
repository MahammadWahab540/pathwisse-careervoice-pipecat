import os
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from transports.base import (
    sanitize_identifier,
    SessionProvisionResult,
    VoiceSessionConfig,
)
from transports.daily_transport import DailyVoiceTransportProvider
from transports.livekit_transport import (
    LiveKitVoiceTransportProvider,
    generate_livekit_token,
)
from transports.factory import TransportRouter
from bot import create_llm_service, notify_careervoice_signal
from server import app

client = TestClient(app)


# ==============================================================================
# 1. Base & Sanitization Tests
# ==============================================================================
def test_sanitize_identifier():
    assert sanitize_identifier("audit_123-abc") == "audit_123-abc"
    assert sanitize_identifier("audit/123@#$xyz") == "audit-123---xyz"
    assert sanitize_identifier("") == "session"
    assert len(sanitize_identifier("a" * 100, max_len=64)) <= 64


# ==============================================================================
# 2. Gemini Model Selection & Configuration Tests
# ==============================================================================
def test_gemini_model_configuration():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key", "GEMINI_MODEL": "gemini-3.6-flash"}):
        with patch("bot.GoogleLLMService") as mock_google_llm:
            create_llm_service("gemini")
            mock_google_llm.assert_called_once_with(
                api_key="fake-key",
                model="gemini-3.6-flash",
            )


def test_gemini_model_custom_override():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key", "GEMINI_MODEL": "custom-gemini-model"}):
        with patch("bot.GoogleLLMService") as mock_google_llm:
            create_llm_service("gemini")
            mock_google_llm.assert_called_once_with(
                api_key="fake-key",
                model="custom-gemini-model",
            )


# ==============================================================================
# 3. Daily Transport Provider Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_daily_provision_session_success():
    provider = DailyVoiceTransportProvider(api_key="fake-daily-key")
    assert provider.is_configured() is True

    with patch.object(
        DailyVoiceTransportProvider,
        "_create_daily_room",
        new_callable=AsyncMock,
        return_value=("https://careervoice.daily.co/test-room", "test-room"),
    ), patch.object(
        DailyVoiceTransportProvider,
        "_create_meeting_token",
        new_callable=AsyncMock,
        side_effect=["student-token-123", "bot-token-456"],
    ):
        result = await provider.provision_session(
            audit_id="audit_test_01",
            target_role="Full Stack Developer",
            student_name="Alex",
        )

        assert isinstance(result, SessionProvisionResult)
        assert result.provider == "daily"
        assert result.audit_id == "audit_test_01"
        assert result.room_url == "https://careervoice.daily.co/test-room"
        assert result.room_name == "test-room"
        assert result.student_token == "student-token-123"
        assert result.bot_token == "bot-token-456"
        assert result.student_token != result.bot_token


# ==============================================================================
# 4. LiveKit Transport Provider Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_livekit_provision_session_success():
    provider = LiveKitVoiceTransportProvider(
        url="wss://careervoice.livekit.cloud",
        api_key="livekit-key-123",
        api_secret="livekit-secret-456-very-long-secret-key-32b",
    )
    assert provider.is_configured() is True

    result = await provider.provision_session(
        audit_id="audit_test_02",
        target_role="AI Engineer",
        student_name="Priya",
    )

    assert isinstance(result, SessionProvisionResult)
    assert result.provider == "livekit"
    assert result.audit_id == "audit_test_02"
    assert result.connection_url == "wss://careervoice.livekit.cloud"
    assert result.room_name == "careervoice-audit_test_02"
    assert result.student_token is not None
    assert result.bot_token is not None
    assert result.student_token != result.bot_token
    assert result.extra["studentIdentity"] == "student-audit_test_02"
    assert result.extra["botIdentity"] == "qalam-audit_test_02"


# ==============================================================================
# 5. Transport Router & Strict vs Failover Policy Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_router_strict_explicit_daily_failure_raises_error():
    router = TransportRouter()
    mock_daily = MagicMock()
    mock_daily.is_configured.return_value = True
    mock_daily.provision_session = AsyncMock(side_effect=RuntimeError("Daily API rate limited"))

    mock_livekit = MagicMock()
    mock_livekit.is_configured.return_value = True

    router._providers["daily"] = mock_daily
    router._providers["livekit"] = mock_livekit

    # Explicit 'daily' must NOT silently switch to livekit in strict mode
    with pytest.raises(RuntimeError, match="Explicitly requested transport 'daily' failed"):
        await router.provision_session_with_failover("a1", "DevOps", requested_transport="daily")


@pytest.mark.asyncio
async def test_router_auto_failover_daily_to_livekit():
    router = TransportRouter()
    mock_daily = MagicMock()
    mock_daily.is_configured.return_value = True
    mock_daily.provision_session = AsyncMock(side_effect=RuntimeError("Daily down"))

    mock_livekit = MagicMock()
    mock_livekit.is_configured.return_value = True
    mock_livekit.provision_session = AsyncMock(
        return_value=SessionProvisionResult(
            provider="livekit",
            audit_id="a3",
            room_url="wss://livekit.cloud",
            room_name="careervoice-a3",
            student_token="st3",
            bot_token="bt3",
            connection_url="wss://livekit.cloud",
        )
    )

    router._providers["daily"] = mock_daily
    router._providers["livekit"] = mock_livekit

    # Omitted requested transport (auto mode) -> failover permitted
    res, prov = await router.provision_session_with_failover("a3", "ML Engineer", requested_transport=None)
    assert res.provider == "livekit"
    assert prov == mock_livekit


@pytest.mark.asyncio
async def test_router_both_fail_raises_runtime_error():
    router = TransportRouter()
    mock_daily = MagicMock()
    mock_daily.is_configured.return_value = True
    mock_daily.provision_session = AsyncMock(side_effect=RuntimeError("Daily down"))

    mock_livekit = MagicMock()
    mock_livekit.is_configured.return_value = True
    mock_livekit.provision_session = AsyncMock(side_effect=RuntimeError("LiveKit down"))

    router._providers["daily"] = mock_daily
    router._providers["livekit"] = mock_livekit

    with pytest.raises(RuntimeError, match="Both primary transport .* failed"):
        await router.provision_session_with_failover("a4", "Cybersecurity", requested_transport=None)


# ==============================================================================
# 6. Service Token Authentication & Production Fail-Closed Tests
# ==============================================================================
def test_production_missing_service_token_fails_closed_401():
    with patch.dict(os.environ, {"APP_ENV": "production", "CAREERVOICE_SERVICE_TOKEN": ""}):
        response = client.post(
            "/api/voice/session",
            json={"auditId": "test_prod_auth", "targetRole": "Engineer"},
        )
        assert response.status_code == 401
        assert "Production environment requires CAREERVOICE_SERVICE_TOKEN" in response.json()["detail"]


def test_production_missing_service_token_fails_readiness_503():
    ready_env = {
        "APP_ENV": "production",
        "CAREERVOICE_SERVICE_TOKEN": "",
        "DAILY_API_KEY": "daily-key",
        "DEEPGRAM_API_KEY": "deepgram-key",
        "CARTESIA_API_KEY": "cartesia-key",
        "GEMINI_API_KEY": "gemini-key",
    }
    with patch.dict(os.environ, ready_env):
        with patch.object(DailyVoiceTransportProvider, "is_configured", return_value=True):
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json()["providers"]["serviceAuth"] is False


def test_auth_missing_token_returns_401():
    with patch.dict(os.environ, {"CAREERVOICE_SERVICE_TOKEN": "secret-token-xyz-123"}):
        response = client.post(
            "/api/voice/session",
            json={"auditId": "test_auth", "targetRole": "Engineer"},
        )
        assert response.status_code == 401
        assert "Missing Authorization" in response.json()["detail"]


def test_auth_invalid_token_returns_401():
    with patch.dict(os.environ, {"CAREERVOICE_SERVICE_TOKEN": "secret-token-xyz-123"}):
        response = client.post(
            "/api/voice/session",
            json={"auditId": "test_auth", "targetRole": "Engineer"},
            headers={"Authorization": "Bearer wrong-token-456"},
        )
        assert response.status_code == 401
        assert "Invalid service token" in response.json()["detail"]


def test_auth_valid_token_accepted():
    with patch.dict(os.environ, {"CAREERVOICE_SERVICE_TOKEN": "secret-token-xyz-123"}):
        with patch(
            "transports.factory.router.provision_session_with_failover",
            new_callable=AsyncMock,
            return_value=(
                SessionProvisionResult(
                    provider="daily",
                    audit_id="test_auth_ok",
                    room_url="https://daily.co/r",
                    room_name="r",
                    student_token="st",
                    bot_token="bt",
                    connection_url="https://daily.co/r",
                ),
                MagicMock(),
            ),
        ):
            response = client.post(
                "/api/voice/session",
                json={"auditId": "test_auth_ok", "targetRole": "Engineer"},
                headers={"Authorization": "Bearer secret-token-xyz-123"},
            )
            assert response.status_code == 200
            assert response.json()["success"] is True


# ==============================================================================
# 7. Backward Compatibility Tests (Pre-provisioned Daily Room)
# ==============================================================================
def test_backward_compatibility_pre_provisioned_room():
    with patch.dict(os.environ, {"APP_ENV": "development", "CAREERVOICE_SERVICE_TOKEN": ""}):
        response = client.post(
            "/api/voice/session",
            json={
                "auditId": "legacy_audit",
                "targetRole": "Frontend Developer",
                "roomUrl": "https://careervoice.daily.co/legacy-room",
                "token": "legacy-token-789",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "daily"
        assert data["roomUrl"] == "https://careervoice.daily.co/legacy-room"
        assert data["token"] == "legacy-token-789"
        assert data["connection"]["url"] == "https://careervoice.daily.co/legacy-room"


# ==============================================================================
# 8. Readiness Semantics Tests (200 vs 503)
# ==============================================================================
def test_readiness_not_ready_returns_503():
    with patch.dict(os.environ, {}, clear=True):
        response = client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["providers"]["deepgram"] is False


def test_readiness_ready_returns_200():
    ready_env = {
        "APP_ENV": "production",
        "CAREERVOICE_SERVICE_TOKEN": "secret-service-token",
        "DAILY_API_KEY": "daily-key-123",
        "DEEPGRAM_API_KEY": "deepgram-key-123",
        "CARTESIA_API_KEY": "cartesia-key-123",
        "GEMINI_API_KEY": "gemini-key-123",
    }
    with patch.dict(os.environ, ready_env):
        with patch.object(
            DailyVoiceTransportProvider, "is_configured", return_value=True
        ):
            response = client.get("/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ready"
            assert data["providers"]["deepgram"] is True
            assert data["providers"]["cartesia"] is True
            assert data["providers"]["llm"] is True
            assert data["providers"]["serviceAuth"] is True


# ==============================================================================
# 9. Evidence Persistence Callback Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_notify_careervoice_signal_execution():
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_post.return_value.__aenter__.return_value = mock_resp

        await notify_careervoice_signal(
            audit_id="audit-evidence-01",
            skill_name="React State Management",
            extracted_level="Advanced",
            confidence_score=92,
            evidence_strength="strong",
            raw_answer="Candidate explained custom Redux toolkit middleware.",
        )

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/api/audit/evidence/signal" in call_args[0][0]
        assert call_args[1]["json"]["auditId"] == "audit-evidence-01"
        assert call_args[1]["json"]["skillName"] == "React State Management"
