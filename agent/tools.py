import os
import logging
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from typing import List
import tempfile
import shutil
from typing import List, Dict, Any
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from agent.config import EMAIL_ENABLED, SENDGRID_API_KEY, logger

def sanitize_excel_data(value) -> Any:
    """Sanitize data to prevent Excel formula interpretation"""
    if isinstance(value, str):
        # If starts with =, +, -, or @, prefix with apostrophe to treat as text
        if value.startswith(('=', '+', '-', '@')):
            return "'" + value
    return value

def save_medicine_to_excel(medicines: List[str], filename="medicine_plan.xlsx") -> str:
    logger.info("📝 Saving medicines to Excel...")

    if not medicines:
        logger.info("⚠️ No medicines found in plan.")
        return "No medicines to save."

    medicine_data = []
    for medicine in medicines:
        sanitized_medicine = sanitize_excel_data(medicine)
        medicine_data.append({"Medicine": sanitized_medicine})

    df = pd.DataFrame(medicine_data)
    file_path = os.path.join("medicine", filename)
    os.makedirs("medicine", exist_ok=True)

    # Atomic write
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    shutil.move(tmp_path, file_path)
    logger.info(f"✅ Saved {len(medicines)} medicines to {file_path}")
    return f"Saved {len(medicines)} medicine records to {file_path}"

def send_email_schedule(details: str, user_email: str, email_content: str = None) -> str:
    print(f"[DEBUG] EMAIL_ENABLED: {EMAIL_ENABLED}")
    print(f"[DEBUG] SENDGRID_API_KEY present: {bool(SENDGRID_API_KEY)}")
    
    if not EMAIL_ENABLED:
        logger.info("Email sending is disabled by EMAIL_ENABLED flag.")
        print("[DEBUG] Email sending is disabled by EMAIL_ENABLED flag.")
        return "Email sending is disabled."
    if not SENDGRID_API_KEY:
        logger.error("SendGrid API key not set.")
        print("[DEBUG] SendGrid API key not set.")
        return "Email service not configured."
    # Use custom email content if provided, otherwise use details
    content_to_send = email_content if email_content else details
    
    message = Mail(
        from_email='jithinjithuedpl922@gmail.com',  # Use your verified sender
        to_emails=user_email,
        subject='Appointment Schedule',
        plain_text_content=content_to_send
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"[DEBUG] SendGrid response status: {response.status_code}")
        print(f"[DEBUG] SendGrid response body: {response.body}")
        print(f"[DEBUG] SendGrid response headers: {response.headers}")
        logger.info(f"✅ Email sent Sucessfully")
        return f"Email sent to {user_email}"
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        print(f"[DEBUG] Exception occurred: {e}")
        return "Failed to send email."