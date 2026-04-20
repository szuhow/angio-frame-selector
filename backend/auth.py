"""
Authentication utilities for Keyselector.
Pure functions – no FastAPI dependency (except HTTPException for convenience).
"""

from __future__ import annotations

import hashlib
import os
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime, timezone, timedelta

import jwt  # PyJWT

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("JWT_SECRET", "keyselector-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256 – stdlib, no native deps)
# ---------------------------------------------------------------------------

_ITERATIONS = 260_000
_HASH_NAME = "sha256"


def hash_password(password: str) -> str:
    """Return 'salt:hash' string."""
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode(), salt, _ITERATIONS)
    return salt.hex() + ":" + h.hex()


def verify_password(password: str, stored: str) -> bool:
    salt_hex, hash_hex = stored.split(":", 1)
    salt = bytes.fromhex(salt_hex)
    h = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode(), salt, _ITERATIONS)
    return secrets.compare_digest(h.hex(), hash_hex)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_jwt(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode & verify JWT. Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------


def generate_api_key() -> str:
    """Generate a prefixed API key: ks_<random>."""
    raw = secrets.token_bytes(32)
    return "ks_" + urlsafe_b64encode(raw).rstrip(b"=").decode()
