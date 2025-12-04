import os
import json
import uuid
import tempfile
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi import Request
import mimetypes
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging
import base64
import io
from fastapi.responses import StreamingResponse

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from auth.google_auth import verify_google_token, create_jwt_token, verify_jwt_token
from auth.middleware import get_current_user, optional_auth
from pipeline.core import MedicalAudioProcessor
from agent.config import set_session_id, logger, GEMINI_API_KEY
from agent.core import process_appointment
from user.chat_service import process_user_question
from database.patient_db import create_patient, get_all_patients, get_patient_by_token, save_soap_record, get_patient_soap_records, get_voice_recordings
from database.supabase_client import supabase
from utils.crypto import decrypt_bytes

load_dotenv()

app = FastAPI(
    title="Medical Audio Processor API",
    description="API for processing medical audio, generating SOAP notes, and executing treatment plans.",
    version="1.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    patient_token_id: str = Form(None)  
):
    """
    Processes an uploaded audio file to generate a medical transcript and SOAP summary.
    If patient_token_id is provided, saves the result to the database.
    """
    
    overall_start = time.time()
    
    
    session_id = set_session_id(session_id or str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] ▶️ Process audio START. Filename: {audio.filename}, Patient Token: {patient_token_id}")

    if not patient_token_id:
        logger.warning(f"[{session_id}] Missing patient_token_id in request - rejecting.")
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
        
        
        if patient_token_id:
            try:
                
                soap_record = save_soap_record(
                    patient_token_id=patient_token_id,
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
    
    
    user_data = verify_google_token(google_token)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    
    
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




@app.get("/patient/{patient_token_id}/soap_records")
async def get_patient_soap_records_api(patient_token_id: str):
    """
    Get all SOAP records for a patient.
    Returns a list of all medical notes with transcripts for the patient.
    """
    try:
        records = get_patient_soap_records(patient_token_id)
        voice_recordings = get_voice_recordings(patient_token_id)
        
        
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
                "patient_token_id": record.get("patient_token_id"),
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
                "patient_token_id": patient_token_id,
                "soap_records": formatted_records,
                "total_records": len(formatted_records)
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Error fetching SOAP records for patient {patient_token_id}: {e}")
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
    Download encrypted audio from Supabase storage, decrypt it server-side,
    and stream back the plaintext audio bytes with the appropriate content-type.

    Query params:
    - storage_path: path in the bucket (e.g. "<patient_token>/<timestamp>_file.wav")
    """
    session_id = set_session_id(str(uuid.uuid4())[:8])
    logger.info(f"[{session_id}] 🎵 Download audio request for: {storage_path}")
    
    try:
        bucket = os.getenv('SUPABASE_STORAGE_BUCKET')
        if not bucket:
            logger.error(f"[{session_id}] SUPABASE_STORAGE_BUCKET not configured")
            raise HTTPException(status_code=500, detail="SUPABASE_STORAGE_BUCKET not configured on server")

        logger.info(f"[{session_id}] 📦 Attempting to download from bucket: {bucket}")
        
        
        try:
            download_res = supabase.storage.from_(bucket).download(storage_path)
            logger.info(f"[{session_id}] ✅ Download response received, type: {type(download_res)}")
        except Exception as download_error:
            logger.error(f"[{session_id}] ❌ Supabase download failed: {download_error}")
            raise HTTPException(status_code=404, detail=f"File not found in storage: {storage_path}")

        
        enc_raw = None
        if isinstance(download_res, bytes):
            enc_raw = download_res
            logger.info(f"[{session_id}] 📥 Downloaded bytes directly, size: {len(enc_raw)} bytes")
        else:
            
            if hasattr(download_res, 'content'):
                enc_raw = download_res.content
                logger.info(f"[{session_id}] 📥 Downloaded via .content, size: {len(enc_raw)} bytes")
            elif hasattr(download_res, 'read'):
                try:
                    enc_raw = download_res.read()
                    logger.info(f"[{session_id}] 📥 Downloaded via .read(), size: {len(enc_raw)} bytes")
                except Exception as read_error:
                    logger.error(f"[{session_id}] ❌ Failed to read response: {read_error}")
                    enc_raw = None

        if enc_raw is None:
            
            if isinstance(download_res, dict) and download_res.get('error'):
                logger.error(f"[{session_id}] ❌ Supabase download error: {download_res.get('error')}")
                raise HTTPException(status_code=500, detail=str(download_res.get('error')))
            logger.error(f"[{session_id}] ❌ Failed to extract bytes from download response")
            raise HTTPException(status_code=500, detail="Failed to download audio from storage")

        
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

            # Validate range
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