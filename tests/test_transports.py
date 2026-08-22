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
from server import app

client = TestClient(app)


# ==============================================================================
# 1. Base & Sanitization Tests
# ==============================================================================
def test_sanitize_identifier():
    assert sanitize_identifier("audit_123-abc") == "audit_123-abc"
    assert sanitize_identifier("audit/123@#$xyz") == "audit-123---xyz"
    assert sanitize_identifier("") == "session"


# ==============================================================================
# 2. Daily Transport Provider Tests
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
# 3. LiveKit Transport Provider Tests
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
# 4. Transport Router & Pre-Session Failover Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_router_explicit_daily():
    router = TransportRouter()
    mock_daily = MagicMock()
    mock_daily.is_configured.return_value = True
    mock_daily.provision_session = AsyncMock(
        return_value=SessionProvisionResult(
            provider="daily",
            audit_id="a1",
            room_url="https://daily.co/r1",
            room_name="r1",
            student_token="st1",
            bot_token="bt1",
            connection_url="https://daily.co/r1",
        )
    )
    router._providers["daily"] = mock_daily

    res, prov = await router.provision_session_with_failover("a1", "DevOps", requested_transport="daily")
    assert res.provider == "daily"
    assert prov == mock_daily


@pytest.mark.asyncio
async def test_router_explicit_livekit():
    router = TransportRouter()
    mock_livekit = MagicMock()
    mock_livekit.is_configured.return_value = True
    mock_livekit.provision_session = AsyncMock(
        return_value=SessionProvisionResult(
            provider="livekit",
            audit_id="a2",
            room_url="wss://livekit.cloud",
            room_name="careervoice-a2",
            student_token="st2",
            bot_token="bt2",
            connection_url="wss://livekit.cloud",
        )
    )
    router._providers["livekit"] = mock_livekit

    res, prov = await router.provision_session_with_failover("a2", "DevOps", requested_transport="livekit")
    assert res.provider == "livekit"
    assert prov == mock_livekit


@pytest.mark.asyncio
async def test_router_failover_daily_to_livekit():
    router = TransportRouter()
    mock_daily = MagicMock()
    mock_daily.is_configured.return_value = True
    mock_daily.provision_session = AsyncMock(side_effect=RuntimeError("Daily API rate limited"))

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

    # Request daily, daily fails, fallback to livekit
    res, prov = await router.provision_session_with_failover("a3", "ML Engineer", requested_transport="daily")
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
        await router.provision_session_with_failover("a4", "Cybersecurity", requested_transport="daily")


# ==============================================================================
# 5. API Endpoints Tests
# ==============================================================================
def test_api_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "daily" in data["transports"]
    assert "livekit" in data["transports"]


def test_api_ready_endpoint():
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert "transports" in data
    assert "daily" in data["transports"]
    assert "livekit" in data["transports"]
    assert "providers" in data


def test_api_start_session_validation_error():
    response = client.post(
        "/api/voice/session",
        json={
            "auditId": "test_bad",
            "targetRole": "Engineer",
            "transport": "unsupported_transport_xyz",
        },
    )
    assert response.status_code == 400
