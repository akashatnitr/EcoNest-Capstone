"""Role-based access control definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import FrozenSet, Iterable


class Role(str, Enum):
    """User roles ordered from least to most privileged."""

    GUEST = "guest"
    FAMILY_MEMBER = "family_member"
    HOMEOWNER = "homeowner"
    SERVICE_ACCOUNT = "service_account"
    SUPERADMIN = "superadmin"


# Permission strings
DEVICE_READ = "device:read"
DEVICE_WRITE = "device:write"
DEVICE_ADMIN = "device:admin"
ROOM_READ = "room:read"
ROOM_WRITE = "room:write"
ROOM_ADMIN = "room:admin"
AGENT_RUN = "agent:run"
AGENT_ADMIN = "agent:admin"
USER_READ = "user:read"
USER_WRITE = "user:write"
USER_ADMIN = "user:admin"


# Role -> permissions matrix
ROLE_PERMISSIONS: dict[Role, FrozenSet[str]] = {
    Role.GUEST: frozenset({DEVICE_READ, ROOM_READ}),
    Role.FAMILY_MEMBER: frozenset(
        {DEVICE_READ, DEVICE_WRITE, ROOM_READ, ROOM_WRITE, AGENT_RUN}
    ),
    Role.HOMEOWNER: frozenset(
        {
            DEVICE_READ,
            DEVICE_WRITE,
            DEVICE_ADMIN,
            ROOM_READ,
            ROOM_WRITE,
            ROOM_ADMIN,
            AGENT_RUN,
            AGENT_ADMIN,
            USER_READ,
            USER_WRITE,
        }
    ),
    Role.SERVICE_ACCOUNT: frozenset({DEVICE_READ, ROOM_READ, AGENT_RUN}),
    Role.SUPERADMIN: frozenset(
        {
            DEVICE_READ,
            DEVICE_WRITE,
            DEVICE_ADMIN,
            ROOM_READ,
            ROOM_WRITE,
            ROOM_ADMIN,
            AGENT_RUN,
            AGENT_ADMIN,
            USER_READ,
            USER_WRITE,
            USER_ADMIN,
        }
    ),
}


@dataclass(frozen=True)
class AccessContext:
    """Resource and request context for ABAC permission checks."""

    user_id: int | None = None
    user_household_id: int | None = None
    resource_household_id: int | None = None
    room_id: int | None = None
    device_id: int | None = None
    allowed_room_ids: FrozenSet[int] = field(default_factory=frozenset)
    allowed_device_ids: FrozenSet[int] = field(default_factory=frozenset)
    current_hour: int | None = None
    allowed_start_hour: int | None = None
    allowed_end_hour: int | None = None


def normalize_role(role: Role | str) -> Role | None:
    """Convert a string or Role value into a Role enum."""
    if isinstance(role, Role):
        return role
    try:
        return Role(role)
    except ValueError:
        return None


def has_permission(role: Role | str, permission: str) -> bool:
    """Return True if the given role includes the permission."""
    normalized = normalize_role(role)
    if normalized is None:
        return False
    return permission in ROLE_PERMISSIONS.get(normalized, frozenset())


def role_rank(role: Role | str) -> int:
    """Return numeric rank for role comparison (higher = more privileged)."""
    normalized = normalize_role(role)
    if normalized is None:
        return -1
    return list(Role).index(normalized)


def has_minimum_role(role: Role | str, minimum: Role | str) -> bool:
    """Return True when role is at least as privileged as minimum."""
    normalized_role = normalize_role(role)
    normalized_minimum = normalize_role(minimum)
    if normalized_role is None or normalized_minimum is None:
        return False
    return role_rank(normalized_role) >= role_rank(normalized_minimum)


def is_admin(role: Role | str) -> bool:
    """Return True for superadmin users."""
    return normalize_role(role) == Role.SUPERADMIN


def is_within_allowed_hours(
    current_hour: int | None,
    start_hour: int | None,
    end_hour: int | None,
) -> bool:
    """Return True when the current hour falls in an allowed window."""
    if current_hour is None or start_hour is None or end_hour is None:
        return True
    if not 0 <= current_hour <= 23:
        return False
    if not 0 <= start_hour <= 23 or not 0 <= end_hour <= 23:
        return False
    if start_hour <= end_hour:
        return start_hour <= current_hour <= end_hour
    return current_hour >= start_hour or current_hour <= end_hour


def current_hour() -> int:
    """Return the current local hour."""
    return datetime.now().hour


def can_access_household(role: Role | str, context: AccessContext) -> bool:
    """Return True if a user can access a household-scoped resource."""
    if is_admin(role):
        return True
    if context.resource_household_id is None:
        return True
    return context.user_household_id == context.resource_household_id


def can_access_room(role: Role | str, context: AccessContext) -> bool:
    """Return True if a user can access a room."""
    if not has_permission(role, ROOM_READ):
        return False
    if is_admin(role):
        return True
    if not can_access_household(role, context):
        return False
    if context.room_id is None or not context.allowed_room_ids:
        return True
    return context.room_id in context.allowed_room_ids


def can_access_device(role: Role | str, context: AccessContext) -> bool:
    """Return True if a user can access a device."""
    if not has_permission(role, DEVICE_READ):
        return False
    if is_admin(role):
        return True
    if not can_access_room(role, context):
        return False
    if context.device_id is None or not context.allowed_device_ids:
        return True
    return context.device_id in context.allowed_device_ids


def can_perform(
    role: Role | str,
    permission: str,
    context: AccessContext | None = None,
) -> bool:
    """Evaluate RBAC plus optional ABAC time restrictions."""
    if not has_permission(role, permission):
        return False
    if context is None:
        return True
    return is_within_allowed_hours(
        context.current_hour,
        context.allowed_start_hour,
        context.allowed_end_hour,
    )


def to_frozenset(values: Iterable[int] | None) -> FrozenSet[int]:
    """Convert optional integer values to a frozenset."""
    return frozenset(values or ())
