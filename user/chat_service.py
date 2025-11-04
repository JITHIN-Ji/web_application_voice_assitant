"""
User Chat Service
Handles user questions, determines if they're related to SOAP summary,
and answers using Gemini 2.5 Flash LLM.
"""
import logging
import google.generativeai as genai
from typing import Dict, Any
from agent.config import logger, GEMINI_API_KEY

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODEL_NAME = "gemini-2.5-flash"
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# Prompt to check if question is related to SOAP summary
QUESTION_RELEVANCE_PROMPT = """
You are a medical assistant analyzing if a patient's question is related to their SOAP (Subjective, Objective, Assessment, Plan) medical summary.

SOAP Summary:
Subjective (S): {subjective}
Objective (O): {objective}
Assessment (A): {assessment}
Plan (P): {plan}

Patient Question: "{question}"

Analyze if the question is directly related to the information in the SOAP summary above.
A question is related if it asks about:
- Information mentioned in any SOAP section (S, O, A, or P)
- Medications, treatments, or plans from the summary
- Symptoms, conditions, or assessments discussed
- Follow-up appointments or recommendations

A question is NOT related if it asks about:
- General medical information not in the summary
- Conditions or symptoms not mentioned in the summary
- Medications or treatments not in the plan
- Questions about other medical issues unrelated to this consultation

Respond with ONLY one word: "YES" if the question is related to the SOAP summary, or "NO" if it is not related.
"""

# Prompt to answer questions based on SOAP summary
ANSWER_QUESTION_PROMPT = """
You are a helpful medical assistant. Answer the patient's question based ONLY on the information provided in their SOAP medical summary.

SOAP Summary:
Subjective (S): {subjective}
Objective (O): {objective}
Assessment (A): {assessment}
Plan (P): {plan}

Patient Question: "{question}"

Instructions:
1. Answer the question based ONLY on the information in the SOAP summary above
2. Be clear, concise, and helpful
3. If specific information is not in the summary, say so clearly
4. Do not provide medical advice beyond what's in the summary
5. Use a friendly, professional tone
6. Keep your answer brief (2-3 sentences maximum)

Answer:
"""

# Message for out-of-context questions
OUT_OF_CONTEXT_MESSAGE = "I understand you have a question, but it's not directly related to your recent consultation summary. I'm forwarding your message to your doctor, and they will get back to you soon. Is there anything else I can help you with regarding your recent visit?"


def check_question_relevance(question: str, soap_summary: Dict[str, str]) -> bool:
    """
    Check if a question is related to the SOAP summary using Gemini.
    
    Args:
        question: The user's question
        soap_summary: Dictionary with keys S, O, A, P (or Subjective, Objective, Assessment, Plan)
    
    Returns:
        True if question is related, False otherwise
    """
    try:
        # Normalize SOAP keys
        subjective = soap_summary.get('S') or soap_summary.get('Subjective') or ''
        objective = soap_summary.get('O') or soap_summary.get('Objective') or ''
        assessment = soap_summary.get('A') or soap_summary.get('Assessment') or ''
        plan = soap_summary.get('P') or soap_summary.get('Plan') or ''
        
        # If SOAP summary is empty, treat as not relevant
        if not any([subjective, objective, assessment, plan]):
            logger.warning("Empty SOAP summary provided for relevance check")
            return False
        
        prompt = QUESTION_RELEVANCE_PROMPT.format(
            subjective=subjective,
            objective=objective,
            assessment=assessment,
            plan=plan,
            question=question
        )
        
        logger.info(f"Checking question relevance for: {question[:50]}...")
        response = gemini_model.generate_content(prompt)
        answer = response.text.strip().upper()
        
        is_relevant = answer.startswith("YES")
        logger.info(f"Question relevance: {is_relevant}")
        return is_relevant
        
    except Exception as e:
        logger.error(f"Error checking question relevance: {e}")
        # On error, default to not relevant to be safe
        return False


def answer_question(question: str, soap_summary: Dict[str, str]) -> str:
    """
    Answer a question based on the SOAP summary using Gemini.
    
    Args:
        question: The user's question
        soap_summary: Dictionary with keys S, O, A, P (or Subjective, Objective, Assessment, Plan)
    
    Returns:
        Answer string based on SOAP summary
    """
    try:
        # Normalize SOAP keys
        subjective = soap_summary.get('S') or soap_summary.get('Subjective') or ''
        objective = soap_summary.get('O') or soap_summary.get('Objective') or ''
        assessment = soap_summary.get('A') or soap_summary.get('Assessment') or ''
        plan = soap_summary.get('P') or soap_summary.get('Plan') or ''
        
        prompt = ANSWER_QUESTION_PROMPT.format(
            subjective=subjective,
            objective=objective,
            assessment=assessment,
            plan=plan,
            question=question
        )
        
        logger.info(f"Generating answer for question: {question[:50]}...")
        response = gemini_model.generate_content(prompt)
        answer = response.text.strip()
        
        logger.info("Answer generated successfully")
        return answer
        
    except Exception as e:
        logger.error(f"Error generating answer: {e}")
        return "I apologize, but I'm having trouble processing your question right now. Please try again or contact your doctor directly."


def process_user_question(question: str, soap_summary: Dict[str, str]) -> Dict[str, Any]:
    """
    Process a user question: check relevance and answer if related, or return out-of-context message.
    
    Args:
        question: The user's question
        soap_summary: Dictionary with keys S, O, A, P
    
    Returns:
        Dictionary with:
            - is_relevant: bool
            - answer: str (answer or out-of-context message)
            - forwarded_to_doctor: bool (True if not relevant)
    """
    try:
        # Check if question is related to SOAP summary
        is_relevant = check_question_relevance(question, soap_summary)
        
        if is_relevant:
            # Answer the question using SOAP summary
            answer = answer_question(question, soap_summary)
            return {
                "is_relevant": True,
                "answer": answer,
                "forwarded_to_doctor": False
            }
        else:
            # Return out-of-context message
            return {
                "is_relevant": False,
                "answer": OUT_OF_CONTEXT_MESSAGE,
                "forwarded_to_doctor": True
            }
            
    except Exception as e:
        logger.error(f"Error processing user question: {e}")
        return {
            "is_relevant": False,
            "answer": "I apologize, but I'm having trouble processing your question right now. Please try again or contact your doctor directly.",
            "forwarded_to_doctor": False
        }

