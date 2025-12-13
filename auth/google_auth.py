import os
from google.oauth2 import id_token
from google.auth.transport import requests
from typing import Optional, Dict
import jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
REFRESH_TOKEN_SECRET_KEY = os.getenv('REFRESH_TOKEN_SECRET_KEY', 'refresh-secret-key-change-in-prod')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 24
JWT_REFRESH_TOKEN_EXPIRATION_DAYS = 7
TOKEN_REFRESH_INTERVAL_HOURS = 1

def verify_google_token(token: str) -> Optional[Dict]:
    """Verify Google OAuth token and return user info."""
    try:
        idinfo = id_token.verify_oauth2_token(
            token, 
            requests.Request(), 
            GOOGLE_CLIENT_ID
        )
        
        if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
            raise ValueError('Wrong issuer.')
        
        return {
            'email': idinfo['email'],
            'name': idinfo.get('name', ''),
            'picture': idinfo.get('picture', ''),
            'sub': idinfo['sub']  
        }
    except ValueError as e:
        print(f"Token verification failed: {e}")
        return None

def create_jwt_token(user_data: Dict) -> str:
    """Create JWT token for authenticated user."""
    payload = {
        'email': user_data['email'],
        'name': user_data['name'],
        'picture': user_data['picture'],
        'sub': user_data['sub'],
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        'iat': datetime.utcnow(),
        'type': 'access'
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def create_refresh_token(user_data: Dict) -> str:
    """Create refresh token for authenticated user."""
    payload = {
        'email': user_data['email'],
        'sub': user_data['sub'],
        'exp': datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_EXPIRATION_DAYS),
        'iat': datetime.utcnow(),
        'type': 'refresh'
    }
    return jwt.encode(payload, REFRESH_TOKEN_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_jwt_token(token: str) -> Optional[Dict]:
    """Verify JWT token and return user data."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != 'access':
            print("Invalid token type")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        print("Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Invalid token: {e}")
        return None

def verify_refresh_token(token: str) -> Optional[Dict]:
    """Verify refresh token and return user data."""
    try:
        payload = jwt.decode(token, REFRESH_TOKEN_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != 'refresh':
            print("Invalid token type")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        print("Refresh token has expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Invalid refresh token: {e}")
        return None