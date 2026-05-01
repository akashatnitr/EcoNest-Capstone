"""User management API routes."""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.core.permissions import (
    USER_ADMIN,
    USER_WRITE,
    has_permission,
)

router = APIRouter(prefix="/users", tags=["users"])


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class GrantAccessRequest(BaseModel):
    room_id: Optional[int] = None
    device_id: Optional[int] = None


def require_admin(current_user: UserProfile) -> None:
    """Raise 403 if current user is not a superadmin."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


@router.get("", response_model=List[UserProfile])
async def list_users(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
):
    """List all users (admin only)."""
    require_admin(current_user)
    result = await session.execute(
        text("SELECT id, email, role, household_id, is_active FROM users")
    )
    rows = result.mappings().all()
    return [UserProfile(**row) for row in rows]


@router.get("/{user_id}", response_model=UserProfile)
async def get_user(
    user_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
):
    """Get user by ID (self or admin)."""
    if current_user.id != user_id and not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access other users' profiles",
        )
    result = await session.execute(
        text(
            "SELECT id, email, role, household_id, is_active FROM users WHERE id = :id"
        ),
        {"id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserProfile(**row)


@router.put("/{user_id}", response_model=UserProfile)
async def update_user(
    user_id: int,
    req: UserUpdate,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
):
    """Update user (self or admin)."""
    if current_user.id != user_id and not has_permission(current_user.role, USER_WRITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update other users",
        )

    # Non-admins cannot change role
    if req.role is not None and not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can change roles",
        )

    fields = []
    params: dict = {"id": user_id}
    if req.email is not None:
        fields.append("email = :email")
        params["email"] = req.email
    if req.role is not None:
        fields.append("role = :role")
        params["role"] = req.role
    if req.is_active is not None:
        fields.append("is_active = :is_active")
        params["is_active"] = req.is_active

    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    await session.execute(
        text(f"UPDATE users SET {', '.join(fields)} WHERE id = :id"),
        params,
    )
    await session.commit()

    result = await session.execute(
        text(
            "SELECT id, email, role, household_id, is_active FROM users WHERE id = :id"
        ),
        {"id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserProfile(**row)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
):
    """Deactivate user (admin only)."""
    require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate yourself",
        )
    await session.execute(
        text("UPDATE users SET is_active = FALSE WHERE id = :id"),
        {"id": user_id},
    )
    await session.commit()
    return None


@router.post("/{user_id}/grant-access", status_code=status.HTTP_204_NO_CONTENT)
async def grant_access(
    user_id: int,
    req: GrantAccessRequest,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
):
    """Grant room or device access to a user (admin only)."""
    require_admin(current_user)
    if req.room_id is None and req.device_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="room_id or device_id required",
        )
    # TODO: implement access control graph insertion once graph layer is ready
    return None
