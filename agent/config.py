import os
import logging
import uuid
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from contextvars import ContextVar
from typing import Dict, Any, Optional


session_id_var: ContextVar[str] = ContextVar('session_id', default='no-session')

class SessionContextFilter(logging.Filter):
    """Filter to inject session ID into log records"""
    def filter(self, record)-> bool:
        record.session_id = session_id_var.get()
        return True
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(session_id)s] - %(levelname)s - %(name)s - %(message)s"
)

# Add the filter to all handlers
for handler in logging.root.handlers:
    handler.addFilter(SessionContextFilter())

logger = logging.getLogger("MedicalAgent")

def set_session_id(session_id: str = None)-> str:
    """Set session ID for logging context"""
    if session_id is None:
        session_id = str(uuid.uuid4())[:8]  # Generate 8-char unique ID
    session_id_var.set(session_id)
    logger.info(f"Session ID set to: {session_id}")
    return session_id
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Optional Deepgram API key for speech-to-text
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY not found in .env file")


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GEMINI_API_KEY,
    temperature=0
)

# LLM Prompt for agent analysis
AGENT_ANALYSIS_PROMPT = """
Analyze this medical plan section and extract information:

Plan: {plan_section}

Please identify:
1. MEDICINES: List any medications, drugs, prescriptions mentioned with complete details
2. APPOINTMENTS: List any follow-up appointments, schedules, or future visits mentioned

Format your response exactly like this:
MEDICINES_FOUND: [If medicines found, list them with complete details separated by semicolons. If none, write "none"]
APPOINTMENT_FOUND: [If appointments found, describe them. If none, write "none"]

For medicines, keep the complete prescription details together (name, dosage, frequency, instructions).
For appointments, include timing, purpose, and any special instructions.
"""