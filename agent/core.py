import logging
from typing import Dict, Any

from agent.config import llm, AGENT_ANALYSIS_PROMPT, logger
from agent.tools import send_email_schedule
from agent.parser import parse_medicines_from_text

# def process_medicines(plan_section: str) -> Dict[str, Any]:
#     logger.info("💊 Processing Medicines...")
#     try:
#         analysis_prompt = AGENT_ANALYSIS_PROMPT.format(plan_section=plan_section)
#         response = llm.invoke(analysis_prompt)
#         analysis = response.content
#         
#         if "MEDICINES_FOUND:" in analysis:
#             medicines_section = analysis.split("MEDICINES_FOUND:")[1]
#             medicines_text = medicines_section.split("APPOINTMENT_FOUND:")[0].strip() \
#                 if "APPOINTMENT_FOUND:" in medicines_section else medicines_section.strip()
#
#             if medicines_text.lower() != "none" and medicines_text.strip():
#                 logger.info(f"Processing medicines: {medicines_text}")
#                 medicines_data = parse_medicines_from_text(medicines_text)
#                 excel_result = save_medicine_to_excel(medicines_data)
#                 return {"status": "success", "result": excel_result}
#
#         return {"status": "success", "result": "No medicines found."}
#
#     except Exception as e:
#         logger.error(f"Medicine processing failed: {e}")
#         return {"status": "error", "error": str(e)}


def process_appointment(plan_section: str, user_email: str, send_email: bool = True, custom_email_content: str = None) -> Dict[str, Any]:
    """
    Process appointment scheduling from the plan section.
    
    Args:
        plan_section: The plan section from SOAP summary
        user_email: Email address to send appointment to
        send_email: If False, only generates email content without sending
        custom_email_content: Optional custom email content (from doctor's edits). 
                              If provided and send_email=True, uses this instead of generating new content.
    
    Returns:
        dict with status, email_content (if send_email=False), result, and error (if any)
    """
    logger.info("📅 Processing Appointment...")
    try:
        analysis_prompt = AGENT_ANALYSIS_PROMPT.format(plan_section=plan_section)
        response = llm.invoke(analysis_prompt)
        analysis = response.content

        if "APPOINTMENT_FOUND:" in analysis:
            appointment_text = analysis.split("APPOINTMENT_FOUND:")[1].strip()

            if appointment_text.lower() != "none" and appointment_text.strip():
                logger.info(f"Processing appointment: {appointment_text}")
                
                
                email_content = generate_appointment_email_content(appointment_text, plan_section)
                
                if not send_email:
                    
                    logger.info("📧 Email preview generated (doctor approval stage)")
                    return {
                        "status": "success",
                        "email_content": email_content,
                        "message": "Email content generated for preview"
                    }
                
                
                
                content_to_send = custom_email_content if custom_email_content else email_content
                email_result = send_email_schedule(appointment_text, user_email, email_content=content_to_send)
                logger.info("📤 Email send attempted via SendGrid")
                
                # Check if email sending was successful
                if email_result.get("status") == "success":
                    return {
                        "status": "success",
                        "result": email_result.get("message"),
                        "message": "Appointment email sent successfully"
                    }
                else:
                    # Email sending failed - return error status
                    error_message = email_result.get("message", "Failed to send email")
                    logger.error(f"Email sending failed: {error_message}")
                    return {
                        "status": "error",
                        "error": error_message,
                        "message": "Failed to send appointment email"
                    }

        return {
            "status": "success",
            "result": "No appointment found.",
            "email_content": "No appointment information found in the plan." if not send_email else None
        }

    except Exception as e:
        logger.error(f"Appointment processing failed: {e}")
        return {"status": "error", "error": str(e)}


def generate_appointment_email_content(appointment_text: str, plan_section: str) -> str:
    """
    Generate email content for appointment scheduling.
    
    Args:
        appointment_text: Extracted appointment information
        plan_section: Full plan section from SOAP summary
    
    Returns:
        Formatted email content as string
    """
    email_content = f"""Subject: Medical Appointment Confirmation

Dear Patient,

This is a confirmation of your upcoming medical appointment based on your recent consultation.

APPOINTMENT DETAILS:
{appointment_text}

FULL TREATMENT PLAN:
{plan_section}

IMPORTANT REMINDERS:
• Please arrive 15 minutes early for check-in
• Bring your ID and insurance card
• Bring a list of current medications
• If you need to reschedule, please contact us at least 24 hours in advance

If you have any questions or concerns, please don't hesitate to contact our office.

Best regards,
Medical Team

---
This is an automated message. Please do not reply to this email.
"""
    return email_content