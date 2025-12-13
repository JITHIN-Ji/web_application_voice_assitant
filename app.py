import os
import json
import uuid
import tempfile
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Cookie
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi import Request
import mimetypes
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging
import base64
import io
from fastapi.responses import StreamingResponse
from fastapi import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from auth.google_auth import verify_google_token, create_jwt_token, verify_jwt_token, create_refresh_token, verify_refresh_token
from auth.middleware import get_current_user, optional_auth
from pipeline.core import MedicalAudioProcessor
from agent.config import set_session_id, logger, GEMINI_API_KEY
from agent.core import process_appointment
from user.chat_service import process_user_question
from database.patient_db import (
    create_patient,
    get_all_patients,
    get_patient_by_id,
    save_soap_record,
    get_patient_soap_records,
    get_voice_recordings,
    get_logged_user_by_email,
    create_logged_user,
    get_or_create_logged_user,
)
from database.azure_client import blob_service_client

from utils.encryption import decrypt_bytes

load_dotenv()
FRONTEND_URLS_ENV = os.getenv('FRONTEND_URLS')
FRONTEND_URL = os.getenv('FRONTEND_URL')

if FRONTEND_URLS_ENV:
    FRONTEND_URLS = [u.strip() for u in FRONTEND_URLS_ENV.split(',') if u.strip()]
elif FRONTEND_URL:
    FRONTEND_URLS = [FRONTEND_URL]
else:
    
    FRONTEND_URLS = ['https://zealous-ground-07c2d0b10.3.azurestaticapps.net']


ENV = os.getenv('ENV', 'development')
COOKIE_SAMESITE = 'none'  
COOKIE_SECURE = True       

app = FastAPI(
    title="Medical Audio Processor API",
    description="API for processing medical audio, generating SOAP notes, and executing treatment plans.",
    version="1.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URLS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info(f"CORS configured to allow origins: {FRONTEND_URLS}")


@app.middleware("http")
async def add_security_and_cache_headers(request: Request, call_next):
    """Global middleware to add browser security headers and prevent client-side caching
    of protected health information (PHI) for sensitive API endpoints.

    - Adds common security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
      Permissions-Policy). Enables HSTS when running in production.
    - For sensitive paths or JSON API responses, sets Cache-Control: no-store and related headers
      to ensure browsers and intermediate caches do not store PHI.
    """

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault("X-XSS-Protection", "0")


    if ENV and ENV.lower() == "production":
        
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")

    
    sensitive_prefixes = (
        "/process_audio",
        "/download_audio",
        "/patients",
        "/patient",
        "/soap_record",
        "/auth",
        "/approve_plan",
        "/user_chat",
    )

    path = request.url.path or ""
    content_type = (response.headers.get("content-type") or "").lower()

    is_sensitive_path = any(path.startswith(p) for p in sensitive_prefixes)
    is_json_response = "application/json" in content_type

    
    if is_sensitive_path or is_json_response:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Backend startup: FastAPI application is initializing.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Backend shutdown: FastAPI application is stopping.")

UPLOAD_FOLDER = 'recordings_backend'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

logger.info("Initializing MedicalAudioProcessor for backend...")
processor = MedicalAudioProcessor(UPLOAD_FOLDER)

@app.get("/")
async def root():
    """Root endpoint to check if the API is running."""
    return {"message": "Medical Audio Processor Backend is running!"}

@app.post("/process_audio")
async def process_audio_api(
    audio: UploadFile = File(...),
    session_id: str = Form(None),
    is_realtime: str = Form(None), 
    patient_id: int = Form(None)
):
    """
    Processes an uploaded audio file to generate a medical transcript and SOAP summary.
    If patient_token_id is provided, saves the result to the database.
    """
    
    overall_start = time.time()
    
    
    session_id = set_session_id(session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] ▶️ Process audio START. Filename: {audio.filename}, Patient ID: {patient_id}")

    if not patient_id:
        logger.warning(f"[{session_id}] Missing patient selection in request - rejecting.")
        raise HTTPException(status_code=400, detail="Patient selection required. Please choose a patient before processing audio.")

    if not audio.filename:
        logger.error(f"[{session_id}] No audio file provided.")
        raise HTTPException(status_code=400, detail="No audio file provided.")

    
    try:
        
        file_save_start = time.time()
        with tempfile.NamedTemporaryFile(delete=False, dir=UPLOAD_FOLDER, suffix=os.path.splitext(audio.filename)[1]) as temp_audio_file:
            contents = await audio.read()
            temp_audio_file.write(contents)
            filepath = temp_audio_file.name
        file_save_time = time.time() - file_save_start
        logger.info(f"[{session_id}] 📁 Audio file saved to {filepath} (Time: {file_save_time:.2f}s)")

        
        logger.info(f"[{session_id}] 🎙️ Starting transcription with Deepgram...")
        transcription_start = time.time()
        transcript, diarized_segments = processor.transcribe_file(filepath)
        transcription_time = time.time() - transcription_start
        
        if not transcript:
            logger.error(f"[{session_id}] Transcription failed for {audio.filename}.")
            raise HTTPException(status_code=500, detail="Failed to transcribe audio.")
        logger.info(f"[{session_id}] ✅ Transcription completed. Transcript length: {len(transcript)} chars (Time: {transcription_time:.2f}s)")

        
        corrected_transcript = transcript
        is_realtime_flag = is_realtime and is_realtime.lower() == "true"
        correction_time = 0
        
        if is_realtime_flag:
            logger.info(f"[{session_id}] 🔧 Option 1 detected: Correcting transcript labels with Gemini...")
            correction_start = time.time()
            corrected_transcript = processor.correct_transcript(transcript)
            correction_time = time.time() - correction_start
            logger.info(f"[{session_id}] ✅ Transcript labels corrected. Using corrected transcript for SOAP generation. (Time: {correction_time:.2f}s)")
        else:
            logger.info(f"[{session_id}] Option 2 detected: Skipping transcript correction.")

        logger.info(f"[{session_id}] 🤖 Passing transcript to Gemini for SOAP creation...")
        soap_start = time.time()
        gemini_summary_raw = processor.query_gemini(corrected_transcript)
        soap_time = time.time() - soap_start
        
        if not gemini_summary_raw:
            logger.error(f"[{session_id}] Gemini summary generation failed for {audio.filename}.")
            raise HTTPException(status_code=500, detail="Failed to generate summary.")

        soap_sections = gemini_summary_raw
        logger.info(f"[{session_id}] 🧴 SOAP sections created: {list(soap_sections.keys()) if isinstance(soap_sections, dict) else 'unknown'} (Time: {soap_time:.2f}s)")

    
        total_time = time.time() - overall_start
        
        
        logger.info(f"[{session_id}] ⏱️ TIMING SUMMARY:")
        logger.info(f"[{session_id}]   • File Save: {file_save_time:.2f}s")
        logger.info(f"[{session_id}]   • Deepgram Transcription: {transcription_time:.2f}s")
        if is_realtime_flag:
            logger.info(f"[{session_id}]   • Gemini Label Correction: {correction_time:.2f}s")
        logger.info(f"[{session_id}]   • Gemini SOAP Creation: {soap_time:.2f}s")
        logger.info(f"[{session_id}]   • TOTAL TIME: {total_time:.2f}s")

        response_data = {
            "transcript": corrected_transcript, 
            "original_transcript": transcript if is_realtime_flag else None,  
            "diarized_segments": diarized_segments,
            "soap_sections": soap_sections,
            "audio_file_name": audio.filename,
            "timing": {
                "file_save_time": round(file_save_time, 2),
                "transcription_time": round(transcription_time, 2),
                "correction_time": round(correction_time, 2) if is_realtime_flag else 0,
                "soap_generation_time": round(soap_time, 2),
                "total_time": round(total_time, 2)
            }
        }
        
        
        if patient_id:
            try:
                
                soap_record = save_soap_record(
                    patient_id=patient_id,
                    audio_file_name=audio.filename,
                    audio_local_path=filepath,
                    transcript=corrected_transcript,
                    original_transcript=transcript if is_realtime_flag else None,
                    soap_sections=soap_sections
                )
                response_data["soap_record_id"] = soap_record['id']
                logger.info(f"[{session_id}] ✅ SOAP record saved to database with ID: {soap_record['id']}")
            except Exception as db_error:
                logger.error(f"[{session_id}] Warning: Failed to save SOAP record to database: {db_error}")
                
        
        logger.info(f"[{session_id}] ⏹️ Process audio END. Success; sending response.")
        return JSONResponse(content=response_data, status_code=200)

    except Exception as e:
        logger.error(f"[{session_id}] Error during audio processing: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"[{session_id}] Cleaned up temporary audio file: {filepath}")


@app.post("/approve_plan")
async def approve_plan_api(payload: dict):
    """
    Approves the extracted medical plan and executes agent actions
    like processing medicines and scheduling appointments.
    """

    overall_start = time.time()
    
    client_session_id = payload.get('session_id') if isinstance(payload, dict) else None
    session_id = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request for plan approval.")

    plan_section = payload.get('plan_section')
    user_email = payload.get('user_email', 'default_patient@example.com') 
    send_email = bool(payload.get('send_email', True))
    custom_email_content = payload.get('email_content')  

    if not plan_section or plan_section.strip().lower() == "n/a":
        logger.warning(f"[{session_id}] No valid plan section provided for approval.")
        return JSONResponse(content={"status": "warning", "message": "No valid plan section provided for approval."}, status_code=200)

    results = {}
    try:
        # Process Medicines (disabled)
        # logger.info(f"[{session_id}] Processing medicines...")
        # medicine_res = process_medicines(plan_section)
        # results['medicine_processing'] = medicine_res

        # Process Appointment - Generate content first (send_email flag controls sending)
        logger.info(f"[{session_id}] Generating appointment email content...")
        preview_start = time.time()
        appointment_preview_res = process_appointment(plan_section, user_email, send_email=False)
        preview_time = time.time() - preview_start
        results['appointment_preview'] = appointment_preview_res
        
        if appointment_preview_res.get("status") == "success":
            logger.info(f"[{session_id}] 📧 Email preview generated for doctor approval. (Time: {preview_time:.2f}s)")

        if appointment_preview_res["status"] == "success" and "email_content" in appointment_preview_res:
            if send_email:
                
                if custom_email_content:
                    logger.info(f"[{session_id}] Sending appointment email with custom (edited) content...")
                else:
                    logger.info(f"[{session_id}] Sending appointment email...")
                
                send_start = time.time()
                appointment_send_res = process_appointment(plan_section, user_email, send_email=True, custom_email_content=custom_email_content)
                send_time = time.time() - send_start
                results['appointment_sending'] = appointment_send_res
                
                if appointment_send_res["status"] == "success":
                    logger.info(f"[{session_id}] ✅ Appointment email sent successfully. (Time: {send_time:.2f}s)")
                    results['message'] = "Plan approved and actions executed (including appointment email)."
                    
                    total_time = time.time() - overall_start
                    logger.info(f"[{session_id}] ⏱️ TIMING SUMMARY:")
                    logger.info(f"[{session_id}]   • Email Preview Generation: {preview_time:.2f}s")
                    logger.info(f"[{session_id}]   • Email Sending: {send_time:.2f}s")
                    logger.info(f"[{session_id}]   • TOTAL TIME: {total_time:.2f}s")
                    
                    logger.info(f"[{session_id}] Plan approved and actions executed successfully.")
                    return JSONResponse(content=results, status_code=200)
                else:
                    # Email sending failed - return error status to frontend
                    error_message = appointment_send_res.get('error', 'Unknown email sending error')
                    logger.error(f"[{session_id}] ❌ Appointment email sending FAILED: {error_message}")
                    results['message'] = f"Failed to send email: {error_message}"
                    results['appointment_sending'] = appointment_send_res
                    
                    return JSONResponse(content=results, status_code=400)
            else:
                results['message'] = "Plan approved. Email content generated for review; sending not requested."
                total_time = time.time() - overall_start
                logger.info(f"[{session_id}] Plan approved; email content generated, not sent. (Total Time: {total_time:.2f}s)")
                return JSONResponse(content=results, status_code=200)
        else:
            results['message'] = "Plan approved, but no appointment found or email generation failed."
            logger.info(f"[{session_id}] No appointment found or email generation failed.")
            return JSONResponse(content=results, status_code=200)

    except Exception as e:
        logger.error(f"[{session_id}] Error during plan approval: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Server error during plan approval: {str(e)}")


@app.post("/user_chat")
async def user_chat_api(payload: dict):
    """
    Handle user questions about their SOAP summary.
    Uses Gemini to determine if question is related to SOAP summary and answers accordingly.
    """
    chat_start = time.time()
    
    client_session_id = payload.get('session_id') if isinstance(payload, dict) else None
    session_id = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received user chat request.")

    question = payload.get('question', '').strip()
    soap_summary = payload.get('soap_summary', {})

    if not question:
        logger.warning(f"[{session_id}] Empty question provided.")
        return JSONResponse(
            content={
                "status": "error",
                "message": "Question cannot be empty.",
                "answer": "Please provide a question."
            },
            status_code=400
        )

    if not soap_summary:
        logger.warning(f"[{session_id}] No SOAP summary provided.")
        return JSONResponse(
            content={
                "status": "error",
                "message": "SOAP summary is required.",
                "answer": "No SOAP summary available. Please visit the Doctor Portal to generate a summary."
            },
            status_code=400
        )

    try:
        
        processing_start = time.time()
        result = process_user_question(question, soap_summary)
        processing_time = time.time() - processing_start
        total_time = time.time() - chat_start
        
        logger.info(
            f"[{session_id}] Question processed. "
            f"Relevant: {result['is_relevant']}, "
            f"Forwarded: {result.get('forwarded_to_doctor', False)} "
            f"(Processing Time: {processing_time:.2f}s, Total Time: {total_time:.2f}s)"
        )
        
        return JSONResponse(
            content={
                "status": "success",
                "is_relevant": result["is_relevant"],
                "answer": result["answer"],
                "forwarded_to_doctor": result.get("forwarded_to_doctor", False),
                "message": "Question processed successfully.",
                "timing": {
                    "processing_time": round(processing_time, 2),
                    "total_time": round(total_time, 2)
                }
            },
            status_code=200
        )

    except Exception as e:
        logger.error(f"[{session_id}] Error processing user question: {e}", exc_info=True)
        return JSONResponse(
            content={
                "status": "error",
                "message": "An error occurred while processing your question.",
                "answer": "I apologize, but I'm having trouble processing your question right now. Please try again or contact your doctor directly."
            },
            status_code=500
        )


@app.post("/patients")
async def create_patient_api(payload: dict, user: dict = Depends(get_current_user)):
    """
    Create a new patient record for the current authenticated user.
    """
    client_session_id = payload.get('session_id') if isinstance(payload, dict) else None
    session_id = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request to create patient")

    name = payload.get('name', '').strip()
    address = payload.get('address', '').strip()
    phone_number = payload.get('phone_number', '').strip()
    problem = payload.get('problem', '').strip()

    if not name:
        return JSONResponse(
            content={
                "status": "error",
                "message": "Patient name is required."
            },
            status_code=400
        )

    try:
        
        logged = get_logged_user_by_email(user['email'])
        if not logged:
            logger.info(f"[{session_id}] Logged user not found for email: {user.get('email')}; creating user record")
            logged = get_or_create_logged_user(user['email'])

        if not logged:
            raise Exception("Authenticated user not found and could not be created")

        patient = create_patient(name, address, phone_number, problem, user_id=logged['id'])
        if patient is None:
            raise Exception("Patient creation failed: returned None")
        logger.info(f"[{session_id}] Patient created: {patient.get('id')}")
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "Patient created successfully.",
                "patient": patient
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"[{session_id}] Error creating patient: {e}", exc_info=True)
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to create patient: {str(e)}"
            },
            status_code=500
        )


@app.get("/patients")
async def get_patients_api(user: dict = Depends(get_current_user), session_id: str = None):
    """
    Get all patients for the current authenticated user.
    """

    client_session_id = session_id
    session_id_obj = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id_obj}] Received request to get patients")

    try:
        logged = get_logged_user_by_email(user['email'])
        if not logged:
            logger.warning(f"[{session_id_obj}] Logged user not found for email: {user.get('email')}")
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Authenticated user not found in database."
                },
                status_code=404
            )
        patients = get_all_patients(user_id=logged['id'])
        logger.info(f"[{session_id_obj}] Retrieved {len(patients)} patients")
        
        return JSONResponse(
            content={
                "status": "success",
                "patients": patients
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"[{session_id_obj}] Error getting patients: {e}", exc_info=True)
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to get patients: {str(e)}"
            },
            status_code=500
        )


@app.get("/patients/{patient_id}")
async def get_patient_api(patient_id: int, user: dict = Depends(get_current_user)):
    """
    Get a patient by token ID (only if it belongs to the current user).
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request to get patient: {patient_id}")

    try:
        logged = get_logged_user_by_email(user['email'])
        patient = get_patient_by_id(patient_id, user_id=logged['id'])
        
        if not patient:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Patient not found or access denied."
                },
                status_code=404
            )
        
        return JSONResponse(
            content={
                "status": "success",
                "patient": patient
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"[{session_id}] Error getting patient: {e}", exc_info=True)
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to get patient: {str(e)}"
            },
            status_code=500
        )
    


@app.post("/auth/google")
async def google_auth(payload: dict, response: Response):  
    """
    Verify Google OAuth token and set JWT token and refresh token in HTTP-only cookies.
    Expects: {"token": "google_oauth_token"}
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Google authentication attempt")
    
    google_token = payload.get('token')
    if not google_token:
        raise HTTPException(status_code=400, detail="Google token is required")
    
    
    user_data = verify_google_token(google_token)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    
    
    jwt_token = create_jwt_token(user_data)
    refresh_token = create_refresh_token(user_data)
    
    
    logger.info(f"[{session_id}] Google authentication succeeded for a user (PII omitted)")

    
    resp = JSONResponse(
        content={
            "status": "success",
            "user": {
                "email": user_data['email'],
                "name": user_data['name'],
                "picture": user_data['picture']
            }
        }
    )

    resp.set_cookie(
        key="auth_token",
        value=jwt_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=86400,      
        path='/'
    )

    resp.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=604800,    
        path='/'
    )

    
    logger.info(f"[{session_id}] auth and refresh cookies set on response (HttpOnly=True, Secure={COOKIE_SECURE}, SameSite={COOKIE_SAMESITE})")

    create_logged_user(user_data['email'])
    logger.info(f"[{session_id}] User authenticated Successfully (login flow complete)")

    return resp
@app.get("/auth/verify")
async def verify_auth(user: dict = Depends(get_current_user)):
    """
    Verify if the current JWT token is valid.
    Requires Authorization header with Bearer token.
    """
    return JSONResponse(
        content={
            "status": "success",
            "user": {
                "email": user['email'],
                "name": user['name'],
                "picture": user.get('picture', '')
            }
        },
        status_code=200
    )

@app.post("/auth/logout")
async def logout(request: Request, response: Response):
    """
    Logout endpoint - deletes the HTTP-only cookie.

    This endpoint no longer requires the authentication dependency so that a client
    can perform logout even when the cookie is not (or cannot be) sent. The handler
    logs whether the cookie was present but does not log any token or PII.
    """

    session_id = set_session_id(str(uuid.uuid4())[:8])

    cookie_present = 'auth_token' in request.cookies
    if cookie_present:
        logger.info(f"[{session_id}] Logout requested; auth cookie present — deleting cookie (no token printed)")
    else:
        logger.info(f"[{session_id}] Logout requested; no auth cookie present. Proceeding to delete cookie on client.")

    response.delete_cookie(
        key="auth_token",
        path='/'
    )

    response.delete_cookie(
        key="refresh_token",
        path='/'
    )

    
    logger.info(f"[{session_id}] Auth and refresh cookies deleted from response (logout) — no PII logged")

    return JSONResponse(content={"status": "success", "message": "Logged out"}, status_code=200)


@app.post("/auth/refresh")
async def refresh_token_endpoint(response: Response, refresh_token: str = Cookie(None)):
    """
    Refresh the access token using the refresh token.
    Expects refresh_token in HTTP-only cookie.
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    
    if not refresh_token:
        logger.info(f"[{session_id}] Token refresh attempted; no refresh token cookie found")
        raise HTTPException(
            status_code=401,
            detail="Refresh token not found"
        )
    
    logger.info(f"[{session_id}] Token refresh attempted; verifying refresh token (value omitted)")
    
    user_data = verify_refresh_token(refresh_token)
    if not user_data:
        logger.info(f"[{session_id}] Token refresh failed; refresh token invalid or expired")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired refresh token"
        )
    
    
    new_access_token = create_jwt_token({
        'email': user_data['email'],
        'name': user_data.get('name', ''),
        'picture': user_data.get('picture', ''),
        'sub': user_data['sub']
    })
    
    logger.info(f"[{session_id}] Token refresh succeeded; new access token generated (value omitted)")
    
    resp = JSONResponse(
        content={
            "status": "success",
            "message": "Token refreshed successfully"
        }
    )
    
    resp.set_cookie(
        key="auth_token",
        value=new_access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=86400,      
        path='/'
    )
    
    logger.info(f"[{session_id}] New access token set in cookie")
    
    return resp


@app.get("/patient/{patient_id}/soap_records")
async def get_patient_soap_records_api(patient_id: int, user: dict = Depends(get_current_user)):
    """
    Get all SOAP records for a patient (only if it belongs to the current user).
    Returns a list of all medical notes with transcripts for the patient.
    """
    try:
        
        logged = get_logged_user_by_email(user['email'])
        patient = get_patient_by_id(patient_id, user_id=logged['id'])
        if not patient:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Patient not found or access denied."
                },
                status_code=404
            )
        
        records = get_patient_soap_records(patient_id)
        voice_recordings = get_voice_recordings(patient_id)
        
        
        voice_recording_map = {}
        for vr in voice_recordings:
            soap_record_id = vr.get('soap_record_id')
            if soap_record_id:
                voice_recording_map[soap_record_id] = vr.get('storage_path')
        
        
        formatted_records = []
        for record in records:
            record_id = record.get("id")
            storage_path = voice_recording_map.get(record_id, record.get("audio_file_name"))
            formatted_records.append({
                "id": record_id,
                "patient_id": record.get("patient_id"),
                "audio_file_name": record.get("audio_file_name"),
                "storage_path": storage_path, 
                "transcript": record.get("transcript"),
                "original_transcript": record.get("original_transcript"),
                "soap_sections": record.get("soap_sections"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at")
            })
        return JSONResponse(
            content={
                "status": "success",
                "patient_id": patient_id,
                "soap_records": formatted_records,
                "total_records": len(formatted_records)
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Error fetching SOAP records for patient {patient_id}: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": str(e)
            },
            status_code=500
        )


@app.put("/soap_record/{record_id}")
async def update_soap_record_api(record_id: int, payload: dict):
    """
    Update SOAP sections for an existing record.
    Used when doctor edits the SOAP summary.
    """
    try:
        from database.patient_db import update_soap_record
        
        soap_sections = payload.get('soap_sections', {})
        
        if not soap_sections:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "No SOAP sections provided"
                },
                status_code=400
            )
        
        success = update_soap_record(record_id, soap_sections)
        
        if success:
            return JSONResponse(
                content={
                    "status": "success",
                    "message": f"SOAP record {record_id} updated successfully",
                    "record_id": record_id
                },
                status_code=200
            )
        else:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Failed to update SOAP record"
                },
                status_code=500
            )
    except Exception as e:
        logger.error(f"Error updating SOAP record {record_id}: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": str(e)
            },
            status_code=500
        )


@app.get("/download_audio")
async def download_audio(request: Request, storage_path: str):
    """
    Download encrypted audio from Azure Blob Storage, decrypt it server-side,
    and stream back the plaintext audio bytes with the appropriate content-type.

    Query params:
    - storage_path: path in the blob container (e.g. "<patient_id>/<timestamp>_file.wav")
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] 🎵 Download audio request for: {storage_path}")
    
    try:
        
        container_name = os.getenv('AZURE_STORAGE_CONTAINER', 'voice-recordings')
        logger.info(f"[{session_id}] 📦 Attempting to download from container: {container_name}")
        
        
        try:
            blob_client = blob_service_client.get_blob_client(
                container=container_name,
                blob=storage_path
            )
            download_stream = blob_client.download_blob()
            enc_raw = download_stream.readall()
            logger.info(f"[{session_id}] ✅ Downloaded {len(enc_raw)} bytes from Azure Blob Storage")
        except Exception as download_error:
            logger.error(f"[{session_id}] ❌ Azure Blob download failed: {download_error}")
            raise HTTPException(status_code=404, detail=f"File not found in storage: {storage_path}")

        
        logger.info(f"[{session_id}] 🔓 Starting decryption...")
        enc_b64 = base64.b64encode(enc_raw).decode('utf-8')
        
        try:
            plaintext = decrypt_bytes(enc_b64)
            logger.info(f"[{session_id}] ✅ Decryption successful, plaintext size: {len(plaintext)} bytes")
        except Exception as decrypt_error:
            logger.error(f"[{session_id}] ❌ Decryption failed: {decrypt_error}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to decrypt audio: {str(decrypt_error)}")

        
        mime_type, _ = mimetypes.guess_type(storage_path)
        if not mime_type:
            mime_type = 'audio/wav'  
            logger.info(f"[{session_id}] ℹ️ Could not determine mime type, using default: {mime_type}")
        else:
            logger.info(f"[{session_id}] 📄 Detected mime type: {mime_type}")

        
        total = len(plaintext)
        range_header = request.headers.get('range')
        
        if range_header:
            logger.info(f"[{session_id}] 📍 Range request received: {range_header}")
            
            try:
                unit, ranges = range_header.split('=')
                start_str, end_str = ranges.split('-')
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else total - 1
            except Exception as parse_error:
                logger.warning(f"[{session_id}] ⚠️ Failed to parse range header, using full range: {parse_error}")
                start = 0
                end = total - 1

            
            if start < 0: start = 0
            if end >= total: end = total - 1
            if start > end:
                logger.error(f"[{session_id}] ❌ Invalid range: {start}-{end}")
                raise HTTPException(status_code=416, detail='Requested Range Not Satisfiable')

            chunk = plaintext[start:end+1]
            headers = {
                'Content-Range': f'bytes {start}-{end}/{total}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(len(chunk)),
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
            logger.info(f"[{session_id}] 📤 Sending partial content: bytes {start}-{end}/{total}")
            return StreamingResponse(io.BytesIO(chunk), status_code=206, media_type=mime_type, headers=headers)

        
        logger.info(f"[{session_id}] 📤 Sending full content: {total} bytes")
        headers = {
            'Accept-Ranges': 'bytes',
            'Content-Length': str(total),
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
        return StreamingResponse(io.BytesIO(plaintext), media_type=mime_type, headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{session_id}] ❌ Unexpected error in download_audio: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
