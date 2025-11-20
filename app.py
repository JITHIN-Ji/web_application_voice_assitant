import os
import json
import uuid
import tempfile
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from auth.google_auth import verify_google_token, create_jwt_token, verify_jwt_token
from auth.middleware import get_current_user, optional_auth
from pipeline.core import MedicalAudioProcessor
from agent.config import set_session_id, logger, GEMINI_API_KEY
from agent.core import process_appointment
from user.chat_service import process_user_question
from database.patient_db import create_patient, get_all_patients, get_patient_by_token

# Load environment variables
load_dotenv()

# --- FastAPI App Setup ---
app = FastAPI(
    title="Medical Audio Processor API",
    description="API for processing medical audio, generating SOAP notes, and executing treatment plans.",
    version="1.0.0"
)

# Configure CORS for local development with React Native
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for development. In production, restrict this.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Lifecycle Logs ---
@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Backend startup: FastAPI application is initializing.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Backend shutdown: FastAPI application is stopping.")

UPLOAD_FOLDER = 'recordings_backend'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Global MedicalAudioProcessor Instance ---
# This ensures models are initialized once when the FastAPI app starts
logger.info("Initializing MedicalAudioProcessor for backend...")
processor = MedicalAudioProcessor(UPLOAD_FOLDER)


# --- API Endpoints ---

@app.get("/")
async def root():
    """Root endpoint to check if the API is running."""
    return {"message": "Medical Audio Processor Backend is running!"}

@app.post("/process_audio")
async def process_audio_api(
    audio: UploadFile = File(...),
    session_id: str = Form(None),
    is_realtime: str = Form(None)  # "true" or "false" string to indicate Option 1 (real-time)
):
    """
    Processes an uploaded audio file to generate a medical transcript and SOAP summary.
    """
    # Track overall timing
    overall_start = time.time()
    
    # Use provided session_id if available, else generate a new one
    session_id = set_session_id(session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] ▶️ Process audio START. Filename: {audio.filename}")

    if not audio.filename:
        logger.error(f"[{session_id}] No audio file provided.")
        raise HTTPException(status_code=400, detail="No audio file provided.")

    
    try:
        # Create a temporary file to save the uploaded audio
        file_save_start = time.time()
        with tempfile.NamedTemporaryFile(delete=False, dir=UPLOAD_FOLDER, suffix=os.path.splitext(audio.filename)[1]) as temp_audio_file:
            contents = await audio.read()
            temp_audio_file.write(contents)
            filepath = temp_audio_file.name
        file_save_time = time.time() - file_save_start
        logger.info(f"[{session_id}] 📁 Audio file saved to {filepath} (Time: {file_save_time:.2f}s)")

        # Transcribe with Deepgram directly (no WAV conversion to avoid large uploads)
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

        # Calculate total time
        total_time = time.time() - overall_start
        
        # Log timing summary
        logger.info(f"[{session_id}] ⏱️ TIMING SUMMARY:")
        logger.info(f"[{session_id}]   • File Save: {file_save_time:.2f}s")
        logger.info(f"[{session_id}]   • Deepgram Transcription: {transcription_time:.2f}s")
        if is_realtime_flag:
            logger.info(f"[{session_id}]   • Gemini Label Correction: {correction_time:.2f}s")
        logger.info(f"[{session_id}]   • Gemini SOAP Creation: {soap_time:.2f}s")
        logger.info(f"[{session_id}]   • TOTAL TIME: {total_time:.2f}s")

        response_data = {
            "transcript": corrected_transcript,  # Return corrected transcript for Option 1, original for Option 2
            "original_transcript": transcript if is_realtime_flag else None,  # Include original for Option 1
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
        
        logger.info(f"[{session_id}] ⏹️ Process audio END. Success; sending response.")
        return JSONResponse(content=response_data, status_code=200)

    except Exception as e:
        logger.error(f"[{session_id}] Error during audio processing: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        # Clean up the temporary audio file
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"[{session_id}] Cleaned up temporary audio file: {filepath}")


@app.post("/approve_plan")
async def approve_plan_api(payload: dict):
    """
    Approves the extracted medical plan and executes agent actions
    like processing medicines and scheduling appointments.
    """
    # Track timing
    overall_start = time.time()
    
    # Reuse client-provided session_id if present to correlate logs across requests
    client_session_id = payload.get('session_id') if isinstance(payload, dict) else None
    session_id = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request for plan approval.")

    plan_section = payload.get('plan_section')
    user_email = payload.get('user_email', 'default_patient@example.com') # Fallback email
    send_email = bool(payload.get('send_email', True))
    custom_email_content = payload.get('email_content')  # Doctor's edited email content

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
                # If email content was generated and sending requested, now actually send it
                # Use custom email content if provided (doctor's edited version)
                if custom_email_content:
                    logger.info(f"[{session_id}] Sending appointment email with custom (edited) content...")
                else:
                    logger.info(f"[{session_id}] Sending appointment email...")
                
                send_start = time.time()
                appointment_send_res = process_appointment(plan_section, user_email, send_email=True, custom_email_content=custom_email_content)
                send_time = time.time() - send_start
                results['appointment_sending'] = appointment_send_res
                
                if appointment_send_res["status"] == "success":
                    logger.info(f"[{session_id}] ✅ Appointment email sent to {user_email}. (Time: {send_time:.2f}s)")
                    results['message'] = "Plan approved and actions executed (including appointment email)."
                    
                    total_time = time.time() - overall_start
                    logger.info(f"[{session_id}] ⏱️ TIMING SUMMARY:")
                    logger.info(f"[{session_id}]   • Email Preview Generation: {preview_time:.2f}s")
                    logger.info(f"[{session_id}]   • Email Sending: {send_time:.2f}s")
                    logger.info(f"[{session_id}]   • TOTAL TIME: {total_time:.2f}s")
                    
                    logger.info(f"[{session_id}] Plan approved and actions executed successfully.")
                    return JSONResponse(content=results, status_code=200)
                else:
                    results['message'] = "Plan approved, but appointment email sending failed."
                    logger.error(f"[{session_id}] Appointment email sending failed: {appointment_send_res.get('error')}")
                    # Still return 200 if other parts succeeded, but indicate partial failure
                    return JSONResponse(content=results, status_code=200)
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
        # Process the question using the chat service
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
async def create_patient_api(payload: dict):
    """
    Create a new patient record.
    """
    client_session_id = payload.get('session_id') if isinstance(payload, dict) else None
    session_id = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request to create patient.")

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
        patient = create_patient(name, address, phone_number, problem)
        logger.info(f"[{session_id}] Patient created: {patient['token_id']}")
        
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
async def get_patients_api(session_id: str = None):
    """
    Get all patients.
    """
    # Use provided session_id or generate new one
    client_session_id = session_id
    session_id = set_session_id(client_session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request to get all patients.")

    try:
        patients = get_all_patients()
        logger.info(f"[{session_id}] Retrieved {len(patients)} patients")
        
        return JSONResponse(
            content={
                "status": "success",
                "patients": patients
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"[{session_id}] Error getting patients: {e}", exc_info=True)
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to get patients: {str(e)}"
            },
            status_code=500
        )


@app.get("/patients/{token_id}")
async def get_patient_api(token_id: str):
    """
    Get a patient by token ID.
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Received request to get patient: {token_id}")

    try:
        patient = get_patient_by_token(token_id)
        
        if not patient:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Patient not found."
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
    

# --- Authentication Endpoints ---

@app.post("/auth/google")
async def google_auth(payload: dict):
    """
    Verify Google OAuth token and return JWT token.
    Expects: {"token": "google_oauth_token"}
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] Google authentication attempt")
    
    google_token = payload.get('token')
    if not google_token:
        raise HTTPException(status_code=400, detail="Google token is required")
    
    # Verify Google token
    user_data = verify_google_token(google_token)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    
    # Create JWT token
    jwt_token = create_jwt_token(user_data)
    
    logger.info(f"[{session_id}] User authenticated: {user_data['email']}")
    
    return JSONResponse(
        content={
            "status": "success",
            "token": jwt_token,
            "user": {
                "email": user_data['email'],
                "name": user_data['name'],
                "picture": user_data['picture']
            }
        },
        status_code=200
    )

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
async def logout(user: dict = Depends(get_current_user)):
    """
    Logout endpoint (client should delete token).
    """
    logger.info(f"User logged out: {user['email']}")
    return JSONResponse(
        content={"status": "success", "message": "Logged out successfully"},
        status_code=200
    )
