import os
import asyncio
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from loguru import logger
import aiohttp

from bot import run_careervoice_agent

app = FastAPI(title="Pathwisse CareerVoice Pipecat Voice Server", version="1.0.0")

DAILY_API_KEY = os.getenv("DAILY_API_KEY", "")
DAILY_API_URL = "https://api.daily.co/v1"


class StartSessionRequest(BaseModel):
    auditId: str
    targetRole: str
    studentName: Optional[str] = "Candidate"
    roomUrl: Optional[str] = None
    token: Optional[str] = None


class StartSessionResponse(BaseModel):
    success: bool
    roomUrl: str
    token: str
    auditId: str


async def create_daily_room_and_token():
    """Creates a temporary Daily.co WebRTC room and tokens for student and Pipecat bot."""
    if not DAILY_API_KEY:
        raise ValueError("DAILY_API_KEY is not configured.")

    headers = {
        "Authorization": f"Bearer {DAILY_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        # Create room
        async with session.post(
            f"{DAILY_API_URL}/rooms",
            headers=headers,
            json={
                "properties": {
                    "exp": int(asyncio.get_event_loop().time()) + 3600,  # 1 hour expiry
                    "enable_chat": False,
                }
            },
        ) as room_resp:
            if room_resp.status != 200:
                text = await room_resp.text()
                raise HTTPException(status_code=500, detail=f"Daily room creation failed: {text}")
            room_data = await room_resp.json()
            room_url = room_data["url"]
            room_name = room_data["name"]

        # Create token for user
        async with session.post(
            f"{DAILY_API_URL}/meeting-tokens",
            headers=headers,
            json={"properties": {"room_name": room_name, "is_owner": False}},
        ) as token_resp:
            user_token_data = await token_resp.json()
            user_token = user_token_data["token"]

        # Create token for bot
        async with session.post(
            f"{DAILY_API_URL}/meeting-tokens",
            headers=headers,
            json={"properties": {"room_name": room_name, "is_owner": True}},
        ) as bot_token_resp:
            bot_token_data = await bot_token_resp.json()
            bot_token = bot_token_data["token"]

    return room_url, user_token, bot_token


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "careervoice-pipecat-voice-agent",
        "aws_ready": True,
    }


@app.post("/api/voice/session", response_model=StartSessionResponse)
async def start_voice_session(req: StartSessionRequest, background_tasks: BackgroundTasks):
    try:
        room_url = req.roomUrl
        user_token = req.token
        bot_token = req.token

        if not room_url or not user_token:
            room_url, user_token, bot_token = await create_daily_room_and_token()

        # Spawn the Pipecat agent background task in the WebRTC room
        background_tasks.add_task(
            run_careervoice_agent,
            room_url=room_url,
            token=bot_token,
            audit_id=req.auditId,
            target_role=req.targetRole,
            student_name=req.studentName,
        )

        return StartSessionResponse(
            success=True,
            roomUrl=room_url,
            token=user_token,
            auditId=req.auditId,
        )
    except Exception as e:
        logger.error(f"Error starting Pipecat voice session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
