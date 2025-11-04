"""
Patient Database Management
SQLite database for storing patient information
"""
import sqlite3
import os
import uuid
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger("PatientDB")

# Database file path
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'patients.db')

def init_db():
    """Initialize the database and create tables if they don't exist"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS patients (
                token_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT,
                phone_number TEXT,
                problem TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Patient database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise


def generate_token_id() -> str:
    """Generate a unique token ID for a patient"""
    return str(uuid.uuid4())


def create_patient(name: str, address: str = '', phone_number: str = '', problem: str = '') -> Dict:
    """Create a new patient record"""
    try:
        token_id = generate_token_id()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO patients (token_id, name, address, phone_number, problem)
            VALUES (?, ?, ?, ?, ?)
        ''', (token_id, name, address, phone_number, problem))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Patient created with token_id: {token_id}")
        
        return {
            'token_id': token_id,
            'name': name,
            'address': address,
            'phone_number': phone_number,
            'problem': problem,
            'status': 'success'
        }
    except Exception as e:
        logger.error(f"Error creating patient: {e}")
        raise


def get_all_patients() -> List[Dict]:
    """Get all patients from the database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT token_id, name, address, phone_number, problem, created_at
            FROM patients
            ORDER BY created_at DESC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        patients = []
        for row in rows:
            patients.append({
                'token_id': row[0],
                'name': row[1],
                'address': row[2],
                'phone_number': row[3],
                'problem': row[4],
                'created_at': row[5]
            })
        
        return patients
    except Exception as e:
        logger.error(f"Error getting patients: {e}")
        raise


def get_patient_by_token(token_id: str) -> Optional[Dict]:
    """Get a patient by token ID"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT token_id, name, address, phone_number, problem, created_at
            FROM patients
            WHERE token_id = ?
        ''', (token_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'token_id': row[0],
                'name': row[1],
                'address': row[2],
                'phone_number': row[3],
                'problem': row[4],
                'created_at': row[5]
            }
        return None
    except Exception as e:
        logger.error(f"Error getting patient: {e}")
        raise


def update_patient(token_id: str, name: str = None, address: str = None, 
                   phone_number: str = None, problem: str = None) -> bool:
    """Update a patient record"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        updates = []
        values = []
        
        if name is not None:
            updates.append('name = ?')
            values.append(name)
        if address is not None:
            updates.append('address = ?')
            values.append(address)
        if phone_number is not None:
            updates.append('phone_number = ?')
            values.append(phone_number)
        if problem is not None:
            updates.append('problem = ?')
            values.append(problem)
        
        if not updates:
            return False
        
        updates.append('updated_at = ?')
        values.append(datetime.now().isoformat())
        values.append(token_id)
        
        query = f'UPDATE patients SET {", ".join(updates)} WHERE token_id = ?'
        cursor.execute(query, values)
        
        conn.commit()
        conn.close()
        
        logger.info(f"Patient updated: {token_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating patient: {e}")
        raise


def delete_patient(token_id: str) -> bool:
    """Delete a patient record"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM patients WHERE token_id = ?', (token_id,))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Patient deleted: {token_id}")
        return True
    except Exception as e:
        logger.error(f"Error deleting patient: {e}")
        raise


# Initialize database on module import
init_db()

