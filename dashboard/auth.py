from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

ALGORITHM = "HS256"
ACCESS_TTL = timedelta(hours=8)

security = HTTPBearer()


def create_token(secret: str, subject: str = "demouser") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + ACCESS_TTL,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_token(
    creds: HTTPAuthorizationCredentials,
    secret: str = "",
) -> dict:
    try:
        payload = jwt.decode(creds.credentials, secret, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def auth_dependency(secret: str):
    def _dep(creds: HTTPAuthorizationCredentials = Depends(security)):
        return verify_token(creds, secret)
    return _dep
