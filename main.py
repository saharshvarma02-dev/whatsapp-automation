"""
main.py — Zero-Cost WhatsApp AI Executive Assistant (Prioritized & Shielded Edition)
====================================================
FastAPI webhook server that:
  • Receives inbound messages from Puppeteer scraper on POST /webhook
  • Logs every message to a local SQLite database
  • Routes the message through Gemini 2.5 Flash (structured output classification)
  • Suppresses automated support replies to low-priority spam/greetings
  • Returns the AI reply directly in the HTTP response body
  • Posts a live-monitor embed to a Discord webhook
"""

import os
import sqlite3
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
import google.genai as genai
from google.genai import types

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("wa-ai-bot")

# ---------------------------------------------------------------------------
# Configuration (all from .env)
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
DB_PATH: str = os.getenv("DB_PATH", "messages.db")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not GEMINI_API_KEY:
    raise EnvironmentError("GEMINI_API_KEY is not set. Add it to your .env file.")

# ---------------------------------------------------------------------------
# Gemini client (module-level singleton)
# ---------------------------------------------------------------------------
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "You are the senior AI Routing and Operations Specialist for SuPrazo Technologies. "
    "Your primary goal is to accurately classify user message intent, determine priority metrics, "
    "and draft clear corporate support replies tailored specifically for a small mobile screen."
)

# ---------------------------------------------------------------------------
# Pydantic schema — Gemini Structured Output contract
# ---------------------------------------------------------------------------
class MessageIntent(BaseModel):
    """Structured output schema enforced via Gemini response_schema."""
    
    priority: Literal["High", "Medium", "Low"] = Field(
        ...,
        description=(
            "Set to 'High' if it is an urgent business lead, internship/job applicant inquiry, or serious problem. "
            "Set to 'Medium' if it is a general question about product specs, services, features, or bootcamps. "
            "Set to 'Low' if it is a standard simple greeting (hi, hello, etc.), thank you, or casual text."
        )
    )
    is_greeting_or_spam: bool = Field(
        ...,
        description="Set to true if the message is an opening casual greeting or spam (like 'hi', 'hey') requiring no automated reply response. Otherwise, false.",
    )
    reply_content: str = Field(
        ...,
        description="The actual conversational response drafted by the AI to be sent back to the user.",
    )
    sender_name: str = Field(
        ...,
        description="The sender's name if explicitly mentioned or inferred in the text context, else 'Unknown'.",
    )


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection with row_factory enabled."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the message_logs table if it does not already exist."""
    ddl = """
        CREATE TABLE IF NOT EXISTS message_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            whatsapp_id   TEXT        NOT NULL,
            sender_name   TEXT        NOT NULL DEFAULT 'Unknown',
            raw_text      TEXT        NOT NULL,
            ai_response   TEXT,
            is_urgent     INTEGER     NOT NULL DEFAULT 0,
            timestamp     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """
    try:
        with get_db_connection() as conn:
            conn.execute(ddl)
            conn.commit()
        log.info("SQLite — table 'message_logs' is ready (db: %s)", DB_PATH)
    except sqlite3.Error as exc:
        log.critical("SQLite — failed to initialise database: %s", exc)
        raise


def db_insert_incoming(whatsapp_id: str, raw_text: str) -> int:
    """
    Insert an incoming message row BEFORE the LLM call.
    Returns the new row id for later UPDATE.
    """
    sql = """
        INSERT INTO message_logs (whatsapp_id, raw_text)
        VALUES (?, ?)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.execute(sql, (whatsapp_id, raw_text))
            conn.commit()
            row_id: int = cursor.lastrowid  # type: ignore[assignment]
            log.info("SQLite — inserted row id=%d for sender=%s", row_id, whatsapp_id)
            return row_id
    except sqlite3.Error as exc:
        log.error("SQLite — insert failed: %s", exc)
        return -1


def db_update_response(
    row_id: int,
    sender_name: str,
    ai_response: str,
    is_urgent_score: int,
) -> None:
    """Patch the existing row with Gemini's response and mapped integer ranking score."""
    sql = """
        UPDATE message_logs
        SET sender_name = ?,
            ai_response = ?,
            is_urgent   = ?
        WHERE id = ?
    """
    try:
        with get_db_connection() as conn:
            conn.execute(sql, (sender_name, ai_response, is_urgent_score, row_id))
            conn.commit()
        log.info("SQLite — updated row id=%d (priority_score=%d)", row_id, is_urgent_score)
    except sqlite3.Error as exc:
        log.error("SQLite — update failed for row id=%d: %s", row_id, exc)


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------
def call_gemini(user_message: str) -> MessageIntent:
    """
    Call Gemini 2.5 Flash with a structured output schema.
    Returns a validated MessageIntent instance.
    """
    analysis_prompt = (
        f"Analyze this incoming user message packet: '{user_message}'.\n\n"
        "Draft a professional customer response tailored for SuPrazo Technologies "
        "covering our dynamic features (HireMe.ai for talent processing, Mock.ai for interview prep, "
        "and Corpool for shared corporate commuting solutions). Fill out all required fields strictly."
    )

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=analysis_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=MessageIntent,
            temperature=0.3,
        ),
    )

    if response.parsed is not None:
        return response.parsed  # type: ignore[return-value]

    import json
    raw_json = response.text or "{}"
    data = json.loads(raw_json)
    return MessageIntent(**data)


# ---------------------------------------------------------------------------
# Discord embed logger (Clean text format)
# ---------------------------------------------------------------------------
async def post_discord_log(
    *,
    sender_name: str,
    whatsapp_id: str,
    raw_text: str,
    ai_reply: str,
    is_urgent_score: int,
) -> None:
    """
    Fire-and-forget Discord webhook with a rich embed.
    Red border + URGENT prefix when priority score equals 2 (High).
    """
    if not DISCORD_WEBHOOK_URL:
        log.warning("Discord — DISCORD_WEBHOOK_URL not set, skipping log.")
        return

    # High = Red (0xFF0000), Medium = Orange (0xFFA500), Low = Gray (0x808080)
    color = 0xFF0000 if is_urgent_score == 2 else (0xFFA500 if is_urgent_score == 1 else 0x808080)
    title_prefix = "URGENT [HIGH] — " if is_urgent_score == 2 else ("📊 [MEDIUM] — " if is_urgent_score == 1 else "☕ [LOW] — ")
    ts = datetime.now(timezone.utc).isoformat()

    embed = {
        "title": f"{title_prefix}WhatsApp Broadcast Log",
        "color": color,
        "timestamp": ts,
        "fields": [
            {"name": "Sender Name", "value": sender_name, "inline": True},
            {"name": "WhatsApp ID", "value": whatsapp_id, "inline": True},
            {"name": "Raw Message", "value": raw_text[:1024], "inline": False},
            {"name": "AI Auto-Reply STATUS", "value": ai_reply[:1024], "inline": False},
        ],
        "footer": {"text": "WA AI Executive Assistant • Powered by Gemini 2.5 Flash"},
    }

    body = {"embeds": [embed]}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(DISCORD_WEBHOOK_URL, json=body)
            resp.raise_for_status()
        log.info("Discord — embed posted successfully (priority_score=%d)", is_urgent_score)
    except Exception as exc:
        log.error("Discord — tracking dispatch logging anomaly: %s", exc)


# ---------------------------------------------------------------------------
# FastAPI app lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks (DB init) then yield for normal operation."""
    log.info("Starting WA AI Executive Assistant ...")
    init_db()
    yield
    log.info("Shutting down WA AI Executive Assistant.")


app = FastAPI(
    title="WA AI Executive Assistant",
    description=(
        "Zero-cost WhatsApp AI chatbot powered by Gemini 2.5 Flash, "
        "OpenWA, SQLite, and Discord monitoring."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Meta"])
async def health_check():
    """Simple liveness probe."""
    return {"status": "ok", "service": "wa-ai-bot", "model": GEMINI_MODEL}


# ---------------------------------------------------------------------------
# Primary webhook endpoint
# ---------------------------------------------------------------------------
@app.post("/webhook", tags=["WhatsApp"])
async def receive_whatsapp_message(request: Request):
    """
    Inbound webhook from Puppeteer scraper.
    Extracts data safely and sends responses straight back down the HTTP stream.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        log.error("Webhook — failed to parse JSON body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if "data" in payload and isinstance(payload["data"], dict):
        target_source = payload.get("data", {})
    else:
        target_source = payload

    raw_text: str = str(target_source.get("body", "")).strip()
    whatsapp_id: str = str(target_source.get("from", "")).strip()

    if not raw_text or not whatsapp_id:
        log.warning("Webhook — missing required string fields; ignoring payload.")
        return {"status": "ignored", "reason": "missing required fields"}

    log.info("Webhook — received message from %s: %r", whatsapp_id, raw_text[:120])

    # 1. Persist incoming message to SQLite (BEFORE LLM call)
    row_id = db_insert_incoming(whatsapp_id, raw_text)

    # 2. Call Gemini for structured AI response
    try:
        intent = call_gemini(raw_text)
        
        # Calculate localized integer priority layout for your existing integer column:
        # High -> 2, Medium -> 1, Low -> 0
        is_urgent_score = 2 if intent.priority == "High" else (1 if intent.priority == "Medium" else 0)
        sender_name = intent.sender_name
        
        # SILENT SHIELD INTERCEPTION LAYER:
        if intent.is_greeting_or_spam:
            log.info("Shield — Caught a casual greeting/spam text. Activating Silent Shield.")
            reply_content = None
            db_response_log = "[Ignored - Casual Greeting]"
        else:
            log.info("Gemini — Valid message intent verified. Priority: %s", intent.priority)
            reply_content = intent.reply_content
            db_response_log = reply_content
            
    except Exception as exc:
        log.error("Gemini — API call failed: %s", exc)
        print(f"\n [CRITICAL GEMINI ERROR] -> {repr(exc)}\n") 
        reply_content = "Thank you for your message. We will respond shortly."
        db_response_log = reply_content
        is_urgent_score = 1
        sender_name = "Unknown"

    # 3. Update SQLite row with structured properties matching existing types
    if row_id != -1:
        db_update_response(row_id, sender_name, db_response_log, is_urgent_score)

    # 4. Post transcript to Discord
    await post_discord_log(
        sender_name=sender_name,
        whatsapp_id=whatsapp_id,
        raw_text=raw_text,
        ai_reply=db_response_log,
        is_urgent_score=is_urgent_score,
    )

    # Return clean payload straight to Puppeteer execution runner
    return {
        "status": "ok",
        "reply": reply_content
    }