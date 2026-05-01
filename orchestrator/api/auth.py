"""Authentication API routes."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import get_settings
from orchestrator.core.database import get_mysql_session
from orchestrator.core.permissions import Role
from orchestrator.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

settings = get_settings()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: Role = Role.HOMEOWNER


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserProfile(BaseModel):
    id: int
    email: str
    role: str
    household_id: Optional[int]
    is_active: bool


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


async def get_current_user(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    session: AsyncSession = Depends(get_mysql_session),
) -> UserProfile:
    """Validate access token and return current user."""
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await session.execute(
        text(
            "SELECT id, email, role, household_id, is_active FROM users WHERE id = :id"
        ),
        {"id": int(user_id)},
    )
    row = result.mappings().first()
    if row is None or not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return UserProfile(**row)


@router.post(
    "/register", response_model=UserProfile, status_code=status.HTTP_201_CREATED
)
async def register(
    req: RegisterRequest,
    session: AsyncSession = Depends(get_mysql_session),
):
    """Register a new user (homeowner only for now)."""
    if req.role != Role.HOMEOWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is limited to homeowner role",
        )

    existing = await session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": req.email},
    )
    if existing.scalar() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    hashed = hash_password(req.password)
    result = await session.execute(
        text(
            "INSERT INTO users (email, hashed_password, role, is_active) "
            "VALUES (:email, :hashed_password, :role, TRUE)"
        ),
        {"email": req.email, "hashed_password": hashed, "role": req.role.value},
    )
    await session.commit()
    user_id = result.lastrowid

    return UserProfile(
        id=user_id,
        email=req.email,
        role=req.role.value,
        household_id=None,
        is_active=True,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    req: RegisterRequest,
    session: AsyncSession = Depends(get_mysql_session),
):
    """OAuth2 password flow — returns access + refresh tokens."""
    result = await session.execute(
        text(
            "SELECT id, email, hashed_password, role, is_active "
            "FROM users WHERE email = :email"
        ),
        {"email": req.email},
    )
    row = result.mappings().first()
    if row is None or not verify_password(req.password, row["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    user_id = str(row["id"])
    access_token = create_access_token({"sub": user_id, "role": row["role"]})
    refresh_token = create_refresh_token({"sub": user_id})

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    await session.execute(
        text(
            "INSERT INTO user_sessions (user_id, refresh_token, expires_at) "
            "VALUES (:user_id, :refresh_token, :expires_at)"
        ),
        {
            "user_id": row["id"],
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        },
    )
    await session.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    req: RefreshRequest,
    session: AsyncSession = Depends(get_mysql_session),
):
    """Refresh access token using a valid refresh token."""
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await session.execute(
        text(
            "SELECT id FROM user_sessions "
            "WHERE user_id = :user_id AND refresh_token = :refresh_token "
            "AND expires_at > NOW()"
        ),
        {"user_id": int(user_id), "refresh_token": req.refresh_token},
    )
    if result.scalar() is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or expired",
        )

    user_result = await session.execute(
        text("SELECT role FROM users WHERE id = :id AND is_active = TRUE"),
        {"id": int(user_id)},
    )
    user_row = user_result.mappings().first()
    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    new_access = create_access_token({"sub": user_id, "role": user_row["role"]})
    return TokenResponse(access_token=new_access, refresh_token=req.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    req: LogoutRequest,
    session: AsyncSession = Depends(get_mysql_session),
):
    """Invalidate refresh token (logout)."""
    if req.refresh_token:
        await session.execute(
            text("DELETE FROM user_sessions WHERE refresh_token = :token"),
            {"token": req.refresh_token},
        )
        await session.commit()
    return None


@router.get("/me", response_model=UserProfile)
async def me(current_user: Annotated[UserProfile, Depends(get_current_user)]):
    """Return current user profile."""
    return current_user
