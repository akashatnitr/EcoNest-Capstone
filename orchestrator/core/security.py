"""Password hashing and JWT token utilities."""

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

import bcrypt
from jose import JWTError, jwt

from orchestrator.config import get_settings

settings = get_settings()


def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    return _create_token(
        data=data,
        token_type="access",
        expires_delta=expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT refresh token."""
    return _create_token(
        data=data,
        token_type="refresh",
        expires_delta=expires_delta
        or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(
    token: str, expected_type: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Decode and validate a JWT token. Returns None if invalid."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        return None

    if expected_type is not None and payload.get("type") != expected_type:
        return None
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, Any], payload)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_refresh_token(token: str, token_hash: str) -> bool:
    """Verify a refresh token against a stored SHA-256 hash."""
    return hmac.compare_digest(hash_refresh_token(token), token_hash)


def _create_token(data: dict, token_type: str, expires_delta: timedelta) -> str:
    """Create a JWT with standard EcoNest claims."""
    now = datetime.now(timezone.utc)
    expire = now + expires_delta
    to_encode = data.copy()
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": str(uuid.uuid4()),
            "type": token_type,
        }
    )
    return str(jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM))
