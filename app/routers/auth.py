"""
Authentication router.

Endpoints:
  POST /auth/register  — Create a new analyst account (username + email + password).
  POST /auth/login     — Exchange credentials for access + refresh tokens.
  POST /auth/refresh   — Exchange a valid refresh token for a new access token.
  GET  /auth/me        — Return the authenticated user's profile.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from jose import jwt, JWTError

from app.db import get_session
from app.models import User
from app.auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    get_current_active_user,
)
from app.schemas import UserCreate, UserOut, Token, RefreshTokenRequest, TokenWithRefresh
from app.config import settings

router = APIRouter(prefix="/auth", tags=["authentication"])


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=201)
async def register(user: UserCreate, session: AsyncSession = Depends(get_session)):
    """Create a new user account."""

    # Username uniqueness
    username_result = await session.execute(
        select(User).where(User.username == user.username)
    )
    if username_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Username already taken")

    # Email uniqueness
    email_result = await session.execute(
        select(User).where(User.email == user.email)
    )
    if email_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password,
    )
    session.add(db_user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that username or email already exists",
        ) from exc
    await session.refresh(db_user)
    return db_user


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenWithRefresh)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    """
    Issue access + refresh tokens.
    The `username` field of the OAuth2 form accepts the analyst's email address.
    """
    result = await session.execute(
        select(User).where(User.email == form_data.username.strip().lower())
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive account")

    token_data = {"sub": user.id}
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=Token)
async def refresh_token(
    body: RefreshTokenRequest,
    session: AsyncSession = Depends(get_session),
):
    """Exchange a valid refresh token for a new short-lived access token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            body.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        # Must be a refresh token, not an access token
        if payload.get("type") != "refresh":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception

    access_token = create_access_token(data={"sub": user.id})
    return {"access_token": access_token, "token_type": "bearer"}


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_active_user)):
    """Return the authenticated user's profile."""
    return current_user
