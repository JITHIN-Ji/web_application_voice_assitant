from fastapi import Request, HTTPException, status, Depends 
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .google_auth import verify_jwt_token
from typing import Optional
from fastapi import Cookie
from agent.config import logger


async def get_current_user(
    auth_token: str = Cookie(None)  
) -> dict:
    """Dependency to get current authenticated user from JWT token in cookie."""
    if not auth_token:
        logger.info("No auth cookie found on incoming request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - no auth cookie found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    
    logger.info("Auth cookie present — verifying token (value omitted)")

    user_data = verify_jwt_token(auth_token)

    if user_data is None:
        logger.info("Auth token invalid or expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info("Auth token verified — user authenticated (no PII logged)")
    return user_data

def optional_auth(request: Request) -> Optional[dict]:
    """Optional authentication - returns user data if authenticated, None otherwise."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.info("No Authorization header present on request (optional auth)")
        return None
    
    token = auth_header.split(" ")[1]
    logger.info("Authorization header present — verifying bearer token (value omitted)")
    return verify_jwt_token(token)

