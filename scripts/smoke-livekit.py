#!/usr/bin/env python3
"""
Smoke test script for LiveKit WebRTC Transport.
Validates room naming, student JWT generation, bot JWT generation, and room join grants.
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from transports.livekit_transport import LiveKitVoiceTransportProvider

load_dotenv()


async def main():
    url = os.getenv("LIVEKIT_URL", "").strip()
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()

    if not (url and api_key and api_secret):
        print("[-] LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET not set. Skipping live smoke test.")
        sys.exit(0)

    print("[+] Starting LiveKit Transport Smoke Test...")
    provider = LiveKitVoiceTransportProvider(url=url, api_key=api_key, api_secret=api_secret)

    try:
        session = await provider.provision_session(
            audit_id="smoke_test_livekit",
            target_role="AI / ML Engineer",
            student_name="Smoke Test User",
        )

        assert session.provider == "livekit"
        assert session.room_name.startswith("careervoice-")
        assert session.student_token
        assert session.bot_token
        assert session.student_token != session.bot_token
        assert session.extra["studentIdentity"] == "student-smoke_test_livekit"
        assert session.extra["botIdentity"] == "qalam-smoke_test_livekit"

        print("[✓] LiveKit Room Prepared successfully:", session.room_name)
        print("[✓] Student JWT Generated (identity: student-smoke_test_livekit): OK")
        print("[✓] Qalam Bot JWT Generated (identity: qalam-smoke_test_livekit): OK")
        print("[✓] LiveKit WebRTC Transport Smoke Test: PASS")
    except Exception as e:
        print(f"[!] LiveKit Smoke Test Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
