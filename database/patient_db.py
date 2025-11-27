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

logger = logging.getLogger("PatientDB")


def generate_token_id() -> str:
    return str(uuid.uuid4())


def create_patient(name: str, address: str = '', phone_number: str = '', problem: str = '') -> Dict:
    token_id = generate_token_id()
    payload = {
        'token_id': token_id,
        'name': name,
        'address': address,
        'phone_number': phone_number,
        'problem': problem,
    }
    res = supabase.table('patients').insert(payload).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase create_patient error: {res.error}")
        raise Exception(res.error)
    logger.info(f"Patient created with token_id: {token_id}")
    payload['status'] = 'success'
    return payload


def get_all_patients() -> List[Dict]:
    res = supabase.table('patients').select('*').order('created_at', desc=True).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_all_patients error: {res.error}")
        raise Exception(res.error)
    return res.data or []


def get_patient_by_token(token_id: str) -> Optional[Dict]:
    res = supabase.table('patients').select('*').eq('token_id', token_id).limit(1).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_patient_by_token error: {res.error}")
        raise Exception(res.error)
    data = res.data or []
    return data[0] if data else None


def update_patient(token_id: str, name: str = None, address: str = None,
                   phone_number: str = None, problem: str = None) -> bool:
    updates = {}
    if name is not None:
        updates['name'] = name
    if address is not None:
        updates['address'] = address
    if phone_number is not None:
        updates['phone_number'] = phone_number
    if problem is not None:
        updates['problem'] = problem
    if not updates:
        return False
    updates['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    res = supabase.table('patients').update(updates).eq('token_id', token_id).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase update_patient error: {res.error}")
        raise Exception(res.error)
    return True


def delete_patient(token_id: str) -> bool:
    res = supabase.table('patients').delete().eq('token_id', token_id).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase delete_patient error: {res.error}")
        raise Exception(res.error)
    return True


def save_soap_record(patient_token_id: str, audio_file_name: str = None, audio_local_path: str = None,
                     transcript: str = '', original_transcript: Optional[str] = None,
                     soap_sections: Optional[Dict] = None) -> Dict:
    """Save a SOAP record and optionally upload audio to Supabase storage.

    Returns inserted record dict (including id) and storage_path if uploaded.
    """
    storage_path = None
    try:
        # Upload file to Supabase Storage if local path provided
        if audio_local_path and os.path.exists(audio_local_path):
            bucket = os.getenv('SUPABASE_STORAGE_BUCKET', )
            timestamp = int(time.time())
            filename = audio_file_name or os.path.basename(audio_local_path)
            storage_path = f"{patient_token_id}/{timestamp}_{filename}"
            with open(audio_local_path, 'rb') as f:
                upload_res = supabase.storage.from_(bucket).upload(storage_path, f)
            # upload_res may be dict-like or response object
            if isinstance(upload_res, dict) and upload_res.get('error'):
                logger.error(f"Supabase storage upload error: {upload_res.get('error')}")
                raise Exception(upload_res.get('error'))

        payload = {
            'patient_token_id': patient_token_id,
            'audio_file_name': audio_file_name,
            'transcript': transcript,
            'original_transcript': original_transcript,
            'soap_sections': soap_sections or {}
        }
        res = supabase.table('soap_records').insert(payload).execute()
        if getattr(res, 'error', None):
            logger.error(f"Supabase insert soap_records error: {res.error}")
            raise Exception(res.error)
        record = (res.data or [None])[0]
        if record is None:
            raise Exception('Failed to insert soap record')
        # If storage_path exists, also insert into voice_recordings table linking to record.id
        if storage_path:
            rec_id = record.get('id')
            voice_payload = {
                'patient_token_id': patient_token_id,
                'soap_record_id': rec_id,
                'storage_path': storage_path,
                'file_name': audio_file_name or os.path.basename(audio_local_path),
                'is_realtime': False
            }
            vres = supabase.table('voice_recordings').insert(voice_payload).execute()
            if getattr(vres, 'error', None):
                logger.error(f"Supabase insert voice_recordings error: {vres.error}")
        record['storage_path'] = storage_path
        return record
    except Exception as e:
        logger.error(f"Error saving SOAP record to Supabase: {e}")
        raise


def get_patient_soap_records(patient_token_id: str) -> List[Dict]:
    res = supabase.table('soap_records').select('*').eq('patient_token_id', patient_token_id).order('created_at', desc=True).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_patient_soap_records error: {res.error}")
        raise Exception(res.error)
    return res.data or []


def get_latest_soap_record(patient_token_id: str) -> Optional[Dict]:
    records = get_patient_soap_records(patient_token_id)
    return records[0] if records else None


def update_soap_record(record_id: int, soap_sections: Dict) -> bool:
    res = supabase.table('soap_records').update({'soap_sections': soap_sections, 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}).eq('id', record_id).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase update_soap_record error: {res.error}")
        raise Exception(res.error)
    return True


def save_voice_recording(patient_token_id: str, soap_record_id: int, file_path: str,
                         file_name: str, is_realtime: bool = False) -> Dict:
    bucket = os.getenv('SUPABASE_STORAGE_BUCKET', 'recordings')
    timestamp = int(time.time())
    storage_path = f"{patient_token_id}/{timestamp}_{file_name}"
    try:
        with open(file_path, 'rb') as f:
            upload_res = supabase.storage.from_(bucket).upload(storage_path, f)
        if isinstance(upload_res, dict) and upload_res.get('error'):
            logger.error(f"Supabase storage upload error: {upload_res.get('error')}")
            raise Exception(upload_res.get('error'))
        payload = {
            'patient_token_id': patient_token_id,
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


def get_voice_recordings(patient_token_id: str) -> List[Dict]:
    res = supabase.table('voice_recordings').select('*').eq('patient_token_id', patient_token_id).order('created_at', desc=True).execute()
    if getattr(res, 'error', None):
        logger.error(f"Supabase get_voice_recordings error: {res.error}")
        raise Exception(res.error)
    return res.data or []



