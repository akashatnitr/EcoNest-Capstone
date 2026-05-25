"""Permission-aware MCP middleware helpers."""

from fastapi import HTTPException, status

from orchestrator.core.permissions import has_permission


def require_permissions(
    role: str,
    permissions: list[str],
) -> None:
    for permission in permissions:
        if not has_permission(role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
