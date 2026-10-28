import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.security import (
    DUMMY_HASH, create_access_token, create_refresh_token,
    decode_token, hash_password, verify_password,
)
from app.models import User
from app.schemas.auth import (
    LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens_for(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    hashed = await run_in_threadpool(hash_password, body.password)
    user = User(username=body.username, email=body.email.lower(), password_hash=hashed)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already taken")
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    # always run one verify, so response time doesn't reveal whether the email exists
    hashed = user.password_hash if user else DUMMY_HASH
    ok = await run_in_threadpool(verify_password, body.password, hashed)

    if user is None or not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _tokens_for(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.InvalidTokenError:  # ExpiredSignatureError is a subclass
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    user = await db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    return _tokens_for(user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user