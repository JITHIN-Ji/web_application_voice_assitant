from fastapi import Request, HTTPException, status, Depends 
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .google_auth import verify_jwt_token
from typing import Optional

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)  
) -> dict:
    """Dependency to get current authenticated user from JWT token."""
    token = credentials.credentials
    user_data = verify_jwt_token(token)
    
    if user_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user_data

def optional_auth(request: Request) -> Optional[dict]:
    """Optional authentication - returns user data if authenticated, None otherwise."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    
    token = auth_header.split(" ")[1]
    return verify_jwt_token(token)
