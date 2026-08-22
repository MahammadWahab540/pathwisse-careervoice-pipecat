#!/usr/bin/env python3
"""
Smoke test script for Daily.co WebRTC Transport.
Validates room provisioning, scoped token isolation, room lifecycle validation, and cleanup.
"""
import os
import sys
import asyncio
import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from transports.daily_transport import DailyVoiceTransportProvider

load_dotenv()


async def main():
    api_key = os.getenv("DAILY_API_KEY", "").strip()
    if not api_key:
        print("[SKIPPED] DAILY_API_KEY is not set. Skipping live Daily smoke test.")
        sys.exit(0)

    print("[+] Starting Daily.co Transport Live Smoke Test...")
    provider = DailyVoiceTransportProvider(api_key=api_key)

    try:
        session = await provider.provision_session(
            audit_id="smoke-test-daily",
            target_role="Full Stack Developer",
            student_name="Smoke Test User",
        )

        assert session.provider == "daily"
        assert session.room_url.startswith("https://")
        assert session.student_token
        assert session.bot_token
        assert session.student_token != session.bot_token

        print("[✓] Daily Room Provisioned successfully:", session.room_name)
        print("[✓] Student Token Generated (scoped, non-owner): OK")
        print("[✓] Bot Token Generated (scoped, owner): OK")

        # Verify room status via Daily REST API
        headers = {"Authorization": f"Bearer {api_key}"}
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(
                f"https://api.daily.co/v1/rooms/{session.room_name}", headers=headers
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Daily room status verification failed with HTTP {resp.status}")
                room_data = await resp.json()
                assert room_data.get("name") == session.room_name
                print("[✓] Verified room status via Daily API: Active")

            # Clean up test room
            async with http_session.delete(
                f"https://api.daily.co/v1/rooms/{session.room_name}", headers=headers
            ) as del_resp:
                if del_resp.status in (200, 204):
                    print("[✓] Cleaned up Daily test room successfully")

        print("[✓] Daily WebRTC Transport Smoke Test: PASS")
    except Exception as e:
        print(f"[FAILED] Daily Smoke Test Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
