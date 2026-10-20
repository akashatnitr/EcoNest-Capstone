"""Authentication API routes."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import (
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
)
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import get_settings
from orchestrator.core.database import get_mysql_session, mysql_session_context
from orchestrator.core.permissions import USER_ADMIN, Role, has_permission
from orchestrator.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
    auto_error=False,
)

settings = get_settings()
ACTIVE_HOUSEHOLD_COOKIE_NAME = "econest_active_household_id"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: Role = Role.HOMEOWNER
    household_profile: Literal[
        "connected_home", "city_apartment", "family_home"
    ] = "connected_home"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserProfile(BaseModel):
    id: int
    email: str
    role: str
    household_id: Optional[int]
    household_name: str | None = None
    is_active: bool


class HouseholdProfile(BaseModel):
    """A household an authenticated user may select as workspace context."""

    id: int
    name: str


class ActiveHouseholdRequest(BaseModel):
    """Requested workspace context for an authenticated user."""

    household_id: int


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


async def get_current_user(
    request: Request,
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    session: AsyncSession = Depends(get_mysql_session),
) -> UserProfile:
    """Validate access token and return current user."""

    token = token or request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
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
        text("""
            SELECT u.id, u.email, u.role, u.household_id, h.name AS household_name,
                   u.is_active
            FROM users AS u
            LEFT JOIN households AS h ON h.id = u.household_id
            WHERE u.id = :id
            """),
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


async def get_optional_current_user(
    request: Request,
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
) -> UserProfile | None:
    """Return the authenticated user when supplied, otherwise no user."""
    token = token or request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
    if token is None:
        return None

    payload = decode_token(token)
    if payload is None or payload.get("type") != "access" or payload.get("sub") is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with mysql_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT u.id, u.email, u.role, u.household_id, h.name AS household_name,
                       u.is_active
                FROM users AS u
                LEFT JOIN households AS h ON h.id = u.household_id
                WHERE u.id = :id
                """
            ),
            {"id": int(payload["sub"])},
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
    "/register",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    req: RegisterRequest,
    session: AsyncSession = Depends(get_mysql_session),
) -> UserProfile:
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

    household_name = _household_profile_name(req.household_profile)
    household_id: int | None = None

    # A connected-home account belongs to the household that Home Assistant
    # already manages.  Reusing that row keeps a registration profile from
    # creating a second record for the same physical house.
    if req.household_profile == "connected_home":
        live_home_result = await session.execute(
            text(
                "SELECT id, name FROM households "
                "WHERE home_assistant_url = :url LIMIT 1"
            ),
            {"url": settings.HA_URL},
        )
        live_home = live_home_result.mappings().first()
        if live_home is not None:
            household_id = int(live_home["id"])
            household_name = str(live_home["name"])

    if household_id is None:
        household_result = await session.execute(
            text(
                "INSERT INTO households (name, home_assistant_url) "
                "VALUES (:name, :home_assistant_url)"
            ),
            {
                "name": household_name,
                "home_assistant_url": (
                    settings.HA_URL if req.household_profile == "connected_home" else None
                ),
            },
        )
        household_id = getattr(household_result, "lastrowid", None)
    if household_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Household was created but no insert id was returned",
        )

    hashed = hash_password(req.password)

    result = await session.execute(
        text("""
            INSERT INTO users (
                email,
                hashed_password,
                role,
                household_id,
                is_active
            )
            VALUES (
                :email,
                :hashed_password,
                :role,
                :household_id,
                TRUE
            )
            """),
        {
            "email": req.email,
            "hashed_password": hashed,
            "role": req.role.value,
            "household_id": household_id,
        },
    )

    await session.commit()

    user_id = getattr(result, "lastrowid", None)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User was created but no insert id was returned",
        )

    return UserProfile(
        id=user_id,
        email=req.email,
        role=req.role.value,
        household_id=int(household_id),
        household_name=household_name,
        is_active=True,
    )


def _household_profile_name(profile: str) -> str:
    """Return the safe display name associated with a registration choice."""
    return {
        "connected_home": "Connected Home",
        "city_apartment": "City Apartment Simulation",
        "family_home": "Family Home Simulation",
    }[profile]


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    response: Response,
    session: AsyncSession = Depends(get_mysql_session),
) -> TokenResponse:
    """OAuth2 password flow."""

    result = await session.execute(
        text("""
            SELECT
                id,
                email,
                hashed_password,
                role,
                is_active
            FROM users
            WHERE email = :email
            """),
        {"email": form_data.username},
    )

    row = result.mappings().first()

    if row is None or not verify_password(
        form_data.password,
        row["hashed_password"],
    ):
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

    access_token = create_access_token(
        {
            "sub": user_id,
            "role": row["role"],
        }
    )

    refresh_token = create_refresh_token(
        {
            "sub": user_id,
        }
    )

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    await session.execute(
        text("""
            INSERT INTO user_sessions (
                user_id,
                refresh_token,
                expires_at
            )
            VALUES (
                :user_id,
                :refresh_token,
                :expires_at
            )
            """),
        {
            "user_id": row["id"],
            "refresh_token": hash_refresh_token(refresh_token),
            "expires_at": expires_at,
        },
    )

    await session.commit()

    response.set_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        value=access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=settings.AUTH_COOKIE_SECURE,
        path="/",
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    req: RefreshRequest,
    response: Response,
    session: AsyncSession = Depends(get_mysql_session),
) -> TokenResponse:
    """Refresh access token."""

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
        text("""
            SELECT refresh_token
            FROM user_sessions
            WHERE user_id = :user_id
            AND expires_at > NOW()
            """),
        {"user_id": int(user_id)},
    )

    stored = result.mappings().first()

    if stored is None or not verify_refresh_token(
        req.refresh_token,
        stored["refresh_token"],
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or expired",
        )

    user_result = await session.execute(
        text("""
            SELECT role
            FROM users
            WHERE id = :id
            AND is_active = TRUE
            """),
        {"id": int(user_id)},
    )

    user_row = user_result.mappings().first()

    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    new_access = create_access_token(
        {
            "sub": user_id,
            "role": user_row["role"],
        }
    )

    response.set_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        value=new_access,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=settings.AUTH_COOKIE_SECURE,
        path="/",
    )
    return TokenResponse(
        access_token=new_access,
        refresh_token=req.refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    req: LogoutRequest,
    response: Response,
    session: AsyncSession = Depends(get_mysql_session),
) -> None:
    """Invalidate refresh token."""

    if req.refresh_token:
        await session.execute(
            text("""
                DELETE FROM user_sessions
                WHERE refresh_token = :token
                """),
            {
                "token": hash_refresh_token(req.refresh_token),
            },
        )

        await session.commit()

    response.delete_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    response.delete_cookie(
        key=ACTIVE_HOUSEHOLD_COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    return None


async def _accessible_households(
    current_user: UserProfile, session: AsyncSession
) -> list[HouseholdProfile]:
    """List only the households the signed-in user may select."""
    if has_permission(current_user.role, USER_ADMIN):
        result = await session.execute(
            text("SELECT id, name FROM households ORDER BY name ASC, id ASC")
        )
    elif current_user.household_id is not None:
        result = await session.execute(
            text("SELECT id, name FROM households WHERE id = :household_id"),
            {"household_id": current_user.household_id},
        )
    else:
        return []
    return [HouseholdProfile(**row) for row in result.mappings().all()]


@router.get("/households", response_model=list[HouseholdProfile])
async def accessible_households(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> list[HouseholdProfile]:
    """Return the household contexts available to the signed-in user."""
    return await _accessible_households(current_user, session)


@router.get("/active-household", response_model=HouseholdProfile | None)
async def active_household(
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> HouseholdProfile | None:
    """Return the selected workspace household, defaulting to the user's home."""
    households = await _accessible_households(current_user, session)
    requested = request.cookies.get(ACTIVE_HOUSEHOLD_COOKIE_NAME)
    try:
        requested_id = int(requested) if requested is not None else None
    except ValueError:
        requested_id = None
    return next(
        (household for household in households if household.id == requested_id),
        next(
            (household for household in households if household.id == current_user.household_id),
            households[0] if households else None,
        ),
    )


@router.post("/active-household", response_model=HouseholdProfile)
async def select_active_household(
    request: ActiveHouseholdRequest,
    response: Response,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> HouseholdProfile:
    """Save a household selection after enforcing the user's access boundary."""
    households = await _accessible_households(current_user, session)
    selected = next(
        (household for household in households if household.id == request.household_id),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to that household.",
        )
    response.set_cookie(
        key=ACTIVE_HOUSEHOLD_COOKIE_NAME,
        value=str(selected.id),
        httponly=True,
        samesite="lax",
        secure=settings.AUTH_COOKIE_SECURE,
        path="/",
    )
    return selected


@router.get("/me", response_model=UserProfile)
async def me(
    current_user: Annotated[
        UserProfile,
        Depends(get_current_user),
    ],
) -> UserProfile:
    """Return current user profile."""

    return current_user
