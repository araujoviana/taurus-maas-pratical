from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from dashboard.auth import (
    ALGORITHM,
    ACCESS_TTL,
    create_token,
    verify_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_token_returns_string():
    token = create_token("my-secret")
    assert isinstance(token, str)
    assert len(token) > 0


def test_verify_valid_token():
    secret = "test-secret-123"
    token = create_token(secret, subject="demouser")
    payload = verify_token(_creds(token), secret)

    assert payload["sub"] == "demouser"
    assert "exp" in payload
    assert "iat" in payload


def test_verify_wrong_secret_raises():
    token = create_token("secret-A")

    with pytest.raises(HTTPException) as exc_info:
        verify_token(_creds(token), "secret-B")

    assert exc_info.value.status_code == 401


def test_verify_expired_token_raises():
    secret = "my-secret"
    now = datetime.now(timezone.utc)
    # Build a token whose exp is already in the past
    payload = {
        "sub": "demouser",
        "iat": now - timedelta(hours=10),
        "exp": now - timedelta(hours=2),
    }
    expired_token = jwt.encode(payload, secret, algorithm=ALGORITHM)

    with pytest.raises(HTTPException) as exc_info:
        verify_token(_creds(expired_token), secret)

    assert exc_info.value.status_code == 401
