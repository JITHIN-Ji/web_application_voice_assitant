"""
Supabase-backed patient database functions.

This module provides the same API used elsewhere in the codebase
but stores data in Supabase Postgres tables and uploads audio to
Supabase Storage.
"""
import os
import uuid
import json
import time
import logging
from typing import List, Dict, Optional

from database.supabase_client import supabase

import io
import base64
import hashlib
from utils.crypto import (
    encrypt_text,
    decrypt_text,
    encrypt_json,
    decrypt_json,
    encrypt_bytes,
    decrypt_bytes,
)


logger = logging.getLogger("PatientDB")


def generate_token_id() -> str:
    return str(uuid.uuid4())


def generate_user_id() -> str:
    return str(uuid.uuid4())


def create_patient(name: str, address: str = '', phone_number: str = '', problem: str = '', user_id: str = '') -> Dict:
    """Create a patient linked to a logged user by `user_id`.

    Note: `user_id` is required to associate patients with a logged user.
    """
    if not user_id:
        raise ValueError('user_id is required to create a patient')

    payload = {
        'user_id': user_id,
        'name': encrypt_text(name),
        'address': encrypt_text(address),
        'phone_number': encrypt_text(phone_number),
        'problem': encrypt_text(problem),
    }
    res = supabase.table('patients').insert(payload).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase create_patient error: {res.error}")
        raise Exception(res.error)
    logger.info(f"Patient created for user_id: {user_id}")
    
    data = (res.data or [None])[0]
    return data or {**payload, 'status': 'success'}


def get_all_patients(user_id: str = '') -> List[Dict]:
    query = supabase.table('patients').select('*')
    if user_id:
        query = query.eq('user_id', user_id)
    res = query.order('created_at', desc=True).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_all_patients error: {res.error}")
        raise Exception(res.error)
    rows = res.data or []
    
    for r in rows:
        try:
            if 'name' in r and r.get('name'):
                r['name'] = decrypt_text(r.get('name'))
            if 'address' in r and r.get('address'):
                r['address'] = decrypt_text(r.get('address'))
            if 'phone_number' in r and r.get('phone_number'):
                r['phone_number'] = decrypt_text(r.get('phone_number'))
            if 'problem' in r and r.get('problem'):
                r['problem'] = decrypt_text(r.get('problem'))
        except Exception:
            logger.exception('Failed to decrypt patient fields')
    return rows


def get_patient_by_id(patient_id: int, user_id: str = '') -> Optional[Dict]:
    res = supabase.table('patients').select('*').eq('id', patient_id).limit(1).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_patient_by_id error: {res.error}")
        raise Exception(res.error)
    data = res.data or []
    patient = data[0] if data else None

    
    if patient and user_id and patient.get('user_id') != user_id:
        logger.warning(f"User {user_id} attempted to access patient {patient_id} belonging to {patient.get('user_id')}")
        return None

    if patient:
        try:
            if patient.get('name'):
                patient['name'] = decrypt_text(patient.get('name'))
            if patient.get('address'):
                patient['address'] = decrypt_text(patient.get('address'))
            if patient.get('phone_number'):
                patient['phone_number'] = decrypt_text(patient.get('phone_number'))
            if patient.get('problem'):
                patient['problem'] = decrypt_text(patient.get('problem'))
        except Exception:
            logger.exception('Failed to decrypt patient')
    return patient


def update_patient(patient_id: int, name: str = None, address: str = None,
                   phone_number: str = None, problem: str = None) -> bool:
    updates = {}
    if name is not None:
        updates['name'] = encrypt_text(name)
    if address is not None:
        updates['address'] = encrypt_text(address)
    if phone_number is not None:
        updates['phone_number'] = encrypt_text(phone_number)
    if problem is not None:
        updates['problem'] = encrypt_text(problem)
    if not updates:
        return False
    updates['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    res = supabase.table('patients').update(updates).eq('id', patient_id).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase update_patient error: {res.error}")
        raise Exception(res.error)
    return True


def delete_patient(patient_id: int) -> bool:
    res = supabase.table('patients').delete().eq('id', patient_id).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase delete_patient error: {res.error}")
        raise Exception(res.error)
    return True


def save_soap_record(patient_id: int, audio_file_name: str = None, audio_local_path: str = None,
                     transcript: str = '', original_transcript: Optional[str] = None,
                     soap_sections: Optional[Dict] = None) -> Dict:
    """Save a SOAP record linked to `patient_id` and optionally upload audio to Supabase storage.

    Returns inserted record dict (including id) and storage_path if uploaded.
    """
    storage_path = None
    try:
        if audio_local_path and os.path.exists(audio_local_path):
            bucket = os.getenv('SUPABASE_STORAGE_BUCKET', )
            timestamp = int(time.time())
            filename = audio_file_name or os.path.basename(audio_local_path)
            storage_path = f"{patient_id}/{timestamp}_{filename}"
            with open(audio_local_path, 'rb') as f:
                raw = f.read()
            try:
                enc_b64 = encrypt_bytes(raw)
                enc_bytes = base64.b64decode(enc_b64)
                upload_res = supabase.storage.from_(bucket).upload(storage_path, enc_bytes)
                logger.info(f"Uploaded audio file to Supabase storage: {storage_path}")
            except Exception:
                logger.exception('Failed to encrypt/upload audio file')
                raise

            if isinstance(upload_res, dict) and upload_res.get('error'):
                logger.error(f"Supabase storage upload error: {upload_res.get('error')}")
                raise Exception(upload_res.get('error'))

        payload = {
            'patient_id': patient_id,
            'audio_file_name': audio_file_name,
            'transcript': encrypt_text(transcript),
            'original_transcript': encrypt_text(original_transcript) if original_transcript is not None else None,
            'soap_sections': encrypt_json(soap_sections or {})
        }
        res = supabase.table('soap_records').insert(payload).execute()
        if getattr(res, 'error', None):
            logger.error(f"Supabase insert soap_records error: {res.error}")
            raise Exception(res.error)
        record = (res.data or [None])[0]
        if record is None:
            raise Exception('Failed to insert soap record')

        if storage_path:
            rec_id = record.get('id')
            voice_payload = {
                'patient_id': patient_id,
                'soap_record_id': rec_id,
                'storage_path': storage_path,
                'file_name': audio_file_name or os.path.basename(audio_local_path),
                'is_realtime': False
            }
            vres = supabase.table('voice_recordings').insert(voice_payload).execute()
            if getattr(vres, 'error', None):
                logger.error(f"Supabase insert voice_recordings error: {vres.error}")
        record['storage_path'] = storage_path

        try:
            if record.get('transcript'):
                record['transcript'] = decrypt_text(record.get('transcript'))
            if record.get('original_transcript'):
                record['original_transcript'] = decrypt_text(record.get('original_transcript'))
            if record.get('soap_sections'):
                record['soap_sections'] = decrypt_json(record.get('soap_sections'))
        except Exception:
            logger.exception('Failed to decrypt soap record')
        return record
    except Exception as e:
        logger.error(f"Error saving SOAP record to Supabase: {e}")
        raise


def get_patient_soap_records(patient_id: int) -> List[Dict]:
    res = supabase.table('soap_records').select('*').eq('patient_id', patient_id).order('created_at', desc=True).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_patient_soap_records error: {res.error}")
        raise Exception(res.error)
    rows = res.data or []
    for r in rows:
        try:
            if r.get('transcript'):
                r['transcript'] = decrypt_text(r.get('transcript'))
            if r.get('original_transcript'):
                r['original_transcript'] = decrypt_text(r.get('original_transcript'))
            if r.get('soap_sections'):
                r['soap_sections'] = decrypt_json(r.get('soap_sections'))
        except Exception:
            logger.exception('Failed to decrypt soap record')
    return rows


def get_latest_soap_record(patient_id: int) -> Optional[Dict]:
    records = get_patient_soap_records(patient_id)
    return records[0] if records else None


def update_soap_record(record_id: int, soap_sections: Dict) -> bool:
    
    enc = encrypt_json(soap_sections)
    res = supabase.table('soap_records').update({'soap_sections': enc, 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}).eq('id', record_id).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase update_soap_record error: {res.error}")
        raise Exception(res.error)
    return True


def save_voice_recording(patient_id: int, soap_record_id: int, file_path: str,
                         file_name: str, is_realtime: bool = False) -> Dict:
    bucket = os.getenv('SUPABASE_STORAGE_BUCKET', 'recordings')
    timestamp = int(time.time())
    storage_path = f"{patient_id}/{timestamp}_{file_name}"
    try:
        with open(file_path, 'rb') as f:
            raw = f.read()
        enc_b64 = encrypt_bytes(raw)
        enc_bytes = base64.b64decode(enc_b64)
        upload_res = supabase.storage.from_(bucket).upload(storage_path, enc_bytes)
        logger.info(f"Uploaded audio file to Supabase storage: {storage_path}")

        if isinstance(upload_res, dict) and upload_res.get('error'):
            logger.error(f"Supabase storage upload error: {upload_res.get('error')}")
            raise Exception(upload_res.get('error'))
        payload = {
            'patient_id': patient_id,
            'soap_record_id': soap_record_id,
            'storage_path': storage_path,
            'file_name': file_name,
            'is_realtime': is_realtime
        }
        res = supabase.table('voice_recordings').insert(payload).execute()
        if getattr(res, 'error', None):
            logger.error(f"Supabase insert voice_recordings error: {res.error}")
            raise Exception(res.error)
        return (res.data or [])[0]
    except Exception as e:
        logger.error(f"Error saving voice recording to Supabase: {e}")
        raise


def get_voice_recordings(patient_id: int) -> List[Dict]:
    res = supabase.table('voice_recordings').select('*').eq('patient_id', patient_id).order('created_at', desc=True).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_voice_recordings error: {res.error}")
        raise Exception(res.error)
    return res.data or []


def create_logged_user(email: str) -> Dict:
    """Create a logged user record storing an encrypted email and returning the generated user id."""
    user_id = generate_user_id()
    
    email_norm = (email or '').strip().lower()
    email_hash = hashlib.sha256(email_norm.encode('utf-8')).hexdigest()
    payload = {
        'id': user_id,
        'email': encrypt_text(email),
        'email_hash': email_hash
    }
    res = supabase.table('logged_users').insert(payload).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase create_logged_user error: {res.error}")
        raise Exception(res.error)
    logger.info(f"Logged user created with id: {user_id}")
    data = (res.data or [None])[0]
    return data or {**payload, 'status': 'success'}


def get_logged_user(user_id: str) -> Optional[Dict]:
    res = supabase.table('logged_users').select('*').eq('id', user_id).limit(1).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_logged_user error: {res.error}")
        raise Exception(res.error)
    rows = res.data or []
    user = rows[0] if rows else None
    if user:
        try:
            if user.get('email'):
                user['email'] = decrypt_text(user.get('email'))
        except Exception:
            logger.exception('Failed to decrypt logged user email')
    return user


def get_logged_user_by_email(email: str) -> Optional[Dict]:
    """Lookup logged user by deterministic email hash (case-insensitive)."""
    email_norm = (email or '').strip().lower()
    email_hash = hashlib.sha256(email_norm.encode('utf-8')).hexdigest()
    res = supabase.table('logged_users').select('*').eq('email_hash', email_hash).limit(1).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_logged_user_by_email error: {res.error}")
        raise Exception(res.error)
    rows = res.data or []
    user = rows[0] if rows else None
    if user:
        try:
            if user.get('email'):
                user['email'] = decrypt_text(user.get('email'))
        except Exception:
            logger.exception('Failed to decrypt logged user email')
    return user


def get_or_create_logged_user(email: str) -> Dict:
    """Return existing logged user by email or create one if missing."""
    existing = get_logged_user_by_email(email)
    if existing:
        return existing
    return create_logged_user(email)



