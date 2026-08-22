# Pathwisse CareerVoice - Pipecat Dual Transport (Daily + LiveKit) Voice Agent

Production-ready, real-time multimodal conversational AI agent for **Pathwisse CareerVoice** built on [Pipecat](https://github.com/pipecat-ai/pipecat).

Supports **Daily.co** and **LiveKit** WebRTC transports behind a unified transport abstraction, with **automatic pre-session failover** and **LLM provider selection** across Google Gemini, Anthropic Claude, and OpenAI GPT-4o.

---

## ⚡ Architecture & Transport Router

```text
CareerVoice Frontend
        ↓
POST /api/voice/session
        ↓
CareerVoice Pipecat
        ↓
Transport Router (Daily ⇄ LiveKit pre-session failover)
   ┌────┴────┐
 Daily     LiveKit
   ↓          ↓
      Pipecat
         ↓
  Deepgram STT
         ↓
CareerVoice Evidence Evaluator (LLM Structured Assessment)
         ↓
    Gemini 3.6 Flash (or Claude / OpenAI fallback)
         ↓
   Cartesia TTS
```

---

## 🚀 Key Capabilities

- **Dual Transport Abstraction**:
  - **Daily.co**: Default WebRTC transport (`VOICE_TRANSPORT_DEFAULT=daily`).
  - **LiveKit**: Fully selectable WebRTC transport (`VOICE_TRANSPORT_FALLBACK=livekit`).
  - Safe pre-session failover if the primary transport fails during room provisioning.
- **Provider-Agnostic Voice Pipeline**:
  - **VAD**: Silero VAD (instant interruption & barge-in detection).
  - **STT**: Deepgram Nova-2 (real-time streaming transcription).
  - **TTS**: Cartesia Sonic (<100ms ultra-low latency synthesis).
  - **LLM**: LLM provider selection based on configured provider availability.
    - **Primary**: Google Gemini (`GEMINI_MODEL=gemini-3.6-flash`).
    - **Alternate configured providers**: Anthropic Claude 3.5 Sonnet / OpenAI GPT-4o-mini.
- **Evidence-Based Assessment & Persistence**:
  - Asynchronously evaluates candidate conversational responses using structured LLM analysis.
  - Verifies concrete engineering evidence (what they built, tools used, architecture decisions, trade-offs, bugs solved) before persisting signals.
  - Vague statements and simple technology mentions ("I know React", "I studied Python") produce no scores and prompt follow-up probes instead.
  - Posts verified signals to CareerVoice backend (`POST /api/audit/evidence/signal`).

---

## 📡 API Contract (`POST /api/voice/session`)

### Request
```json
{
  "auditId": "8f3b6c21-1234-4567-89ab-cdef01234567",
  "targetRole": "Full Stack Developer",
  "studentName": "Alex",
  "transport": "daily"
}
```
*(Note: `transport` is optional. If omitted, `VOICE_TRANSPORT_DEFAULT` is used).*

---

### Response: Daily Provider
```json
{
  "success": true,
  "auditId": "8f3b6c21-1234-4567-89ab-cdef01234567",
  "provider": "daily",
  "roomUrl": "https://careervoice.daily.co/careervoice-8f3b6c21-1724310000",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "connection": {
    "url": "https://careervoice.daily.co/careervoice-8f3b6c21-1724310000",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "roomName": "careervoice-8f3b6c21-1724310000"
  }
}
```

---

### Response: LiveKit Provider
```json
{
  "success": true,
  "auditId": "8f3b6c21-1234-4567-89ab-cdef01234567",
  "provider": "livekit",
  "roomUrl": "wss://careervoice.livekit.cloud",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "connection": {
    "url": "wss://careervoice.livekit.cloud",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "roomName": "careervoice-8f3b6c21-1234-4567-89ab-cdef01234567",
    "extra": {
      "studentIdentity": "student-8f3b6c21-1234-4567-89ab-cdef01234567",
      "botIdentity": "qalam-8f3b6c21-1234-4567-89ab-cdef01234567"
    }
  }
}
```

---

## 🖥️ Frontend Client Connection Guide

The CareerVoice frontend routes connection logic based on `response.provider`:

* **`provider === "daily"`**: Connect using `@daily-co/daily-js` with `connection.url` and `connection.token`.
* **`provider === "livekit"`**: Connect using `livekit-client` (`Room.connect(connection.url, connection.token)`).

---

## 🛠️ Local Development & Testing

```bash
# Clone & create venv
git clone https://github.com/MahammadWahab540/pathwisse-careervoice-pipecat.git
cd pathwisse-careervoice-pipecat
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run Unit & Integration Tests
pytest tests/

# Run Provider Provisioning Smoke Tests
python scripts/smoke-daily.py
python scripts/smoke-livekit.py

# Start Server
python server.py
```

---

## ☁️ AWS Deployment (App Runner + Secrets Manager + OIDC)

Deploy using the automated 1-click script:
```bash
cd deploy
./deploy-apprunner.sh
```
Or via GitHub Actions with OpenID Connect (OIDC) authentication on push to `main`.
