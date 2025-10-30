import os
import logging
import google.generativeai as genai
from dotenv import load_dotenv
import json
from agent.config import logger , GEMINI_API_KEY 
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

GEMINI_MODEL_NAME = "gemini-2.5-flash"
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)


MEDICAL_DIALOGUE_PROMPT_JSON = """
You are a precise medical dialogue extraction assistant.
Extract clinical information from the transcript using SOAP method.
Do NOT summarize. Do NOT generalize. Do NOT skip.

CRITICAL RULES:
1. Return output as valid JSON with exactly these keys: Subjective, Objective, Assessment, Plan.
2. Each value should be a string (short, factual, point-form). If no info, use "N/A".
3. Denied or negative findings go to the appropriate section (Subjective if patient-reported, Objective if clinician-observed).
4. Do NOT include extra text outside the JSON.

Now extract information from ONLY this transcript:

{transcript}

Output (strict JSON only, no extra text):
"""

def query_gemini_summary(transcript: str) -> dict:
    """Send transcript to Gemini model and return structured JSON."""
    prompt = MEDICAL_DIALOGUE_PROMPT_JSON.format(transcript=transcript)
    logger.info(f"🤖 Gemini: Sending transcript for SOAP creation (chars={len(transcript)})")
    logger.debug(f"Prompt sent to Gemini: {prompt}")
    try:
        response = gemini_model.generate_content(
            prompt,
             
        )
        text = response.text.strip() if response and response.text else ""
        logger.debug(f"Raw response from Gemini: {text}")

        # Remove code block markers if present
        if text.startswith("```"):
            # Remove the first line (```json or ```
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove the last line if it's ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # Now try to parse JSON
        try:
            result = json.loads(text)
            # Ensure all keys exist
            for key in ["Subjective", "Objective", "Assessment", "Plan"]:
                if key not in result:
                    result[key] = "N/A"
            logger.info("🤖 Gemini: SOAP sections generated successfully.")
            return result
            
        except json.JSONDecodeError:
            logger.warning(f"Malformed JSON from Gemini: {text}")
            # Fallback: return empty sections
            return {
                "Subjective": "N/A",
                "Objective": "N/A",
                "Assessment": "N/A",
                "Plan": "N/A"
            }
    except Exception as e:
        logger.error(f"Gemini query failed: {e}")
        return {
            "Subjective": "N/A",
            "Objective": "N/A",
            "Assessment": "N/A",
            "Plan": "N/A"
        }
