import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)   # note: argon2 takes (hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Used when the email doesn't exist, so login takes the same time either way
DUMMY_HASH = hash_password("not-a-real-password")


def _create_token(user_id: int, token_type: str, lifetime: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),          # who
        "type": token_type,           # "access" or "refresh"
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,      # unique id, useful for revocation later
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int) -> str:
    return _create_token(user_id, "access", timedelta(minutes=settings.ACCESS_TOKEN_MINUTES))


def create_refresh_token(user_id: int) -> str:
    return _create_token(user_id, "refresh", timedelta(days=settings.REFRESH_TOKEN_DAYS))


def decode_token(token: str, expected_type: str) -> dict:
    """Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError on any problem."""
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload