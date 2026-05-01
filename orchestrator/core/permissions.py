"""Role-based access control definitions."""

from enum import Enum
from typing import FrozenSet


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
    Role.SERVICE_ACCOUNT: frozenset({DEVICE_READ, DEVICE_WRITE, ROOM_READ, AGENT_RUN}),
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


def has_permission(role: Role, permission: str) -> bool:
    """Return True if the given role includes the permission."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def role_rank(role: Role) -> int:
    """Return numeric rank for role comparison (higher = more privileged)."""
    return list(Role).index(role)
