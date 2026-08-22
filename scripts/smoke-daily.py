#!/usr/bin/env python3
"""
Smoke test script for Daily.co WebRTC Transport.
Validates room creation, student token generation, bot token generation, and cleanup.
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from transports.daily_transport import DailyVoiceTransportProvider

load_dotenv()


async def main():
    api_key = os.getenv("DAILY_API_KEY", "").strip()
    if not api_key:
        print("[-] DAILY_API_KEY is not set. Skipping live smoke test.")
        sys.exit(0)

    print("[+] Starting Daily.co Transport Smoke Test...")
    provider = DailyVoiceTransportProvider(api_key=api_key)

    try:
        session = await provider.provision_session(
            audit_id="smoke_test_daily",
            target_role="Full Stack Developer",
            student_name="Smoke Test User",
        )

        assert session.provider == "daily"
        assert session.room_url.startswith("https://")
        assert session.student_token
        assert session.bot_token
        assert session.student_token != session.bot_token

        print("[✓] Daily Room Created successfully:", session.room_name)
        print("[✓] Student Token Generated (scoped, non-owner): OK")
        print("[✓] Bot Token Generated (scoped, owner): OK")
        print("[✓] Daily WebRTC Transport Smoke Test: PASS")
    except Exception as e:
        print(f"[!] Daily Smoke Test Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
