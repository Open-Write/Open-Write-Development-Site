"""JWT authentication + password hashing + FastAPI dependencies.

Provides:
  - hash_password / verify_password (bcrypt via passlib)
  - create_access_token / decode_token (python-jose)
  - get_current_user dependency that also loads the user's settings into the
    per-request contextvar so pipeline/AI code sees the right API keys.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app import db, settings_store
from app.config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    # bcrypt has a 72-byte limit; truncate defensively.
    return _pwd.hash(password[:72])


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password[:72], password_hash)
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + dt.timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Resolve the bearer token to a user row and bind that user's settings.

    Loads the user's settings JSON from the DB and installs it into the
    request-scoped contextvar so the pipeline's settings_store functions
    (get_providers, get_writer_model, ...) resolve to this user's API keys.
    """
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token.",
        )
    payload = decode_token(creds.credentials)
    user_id = payload.get("sub")
    user = db.query_one(
        "SELECT id, email, is_admin, created_at FROM users WHERE id = %s", (user_id,)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found."
        )
    user["id"] = str(user["id"])
    # Bind this user's settings for the duration of the request.
    settings_store.bind_user_settings(user["id"])
    return user
