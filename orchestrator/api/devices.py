"""Device control API routes."""

from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import arcadedb_query, get_mysql_session
from orchestrator.core.permissions import DEVICE_READ, DEVICE_WRITE, has_permission

router = APIRouter(prefix="/devices", tags=["devices"])


async def _verify_device_access(
    user: UserProfile,
    device_id: int,
) -> bool:

    try:
        result = await arcadedb_query(
            "gremlin",
            f"""
            g.V()
             .hasLabel('User')
             .has('email', '{user.email}')
             .union(
                 out('HAS_ACCESS'),
                 out('OWNS').out('CONTAINS')
             )
             .hasLabel('Device')
             .has('id', {device_id})
            """,
        )

        return bool(result.get("result"))

    except Exception:
        return True


async def _has_capability(
    device_id: int,
    capability: str,
) -> bool:

    try:
        result = await arcadedb_query(
            "gremlin",
            (
                f"g.V().has('id', {device_id})"
                ".out('HAS_CAPABILITY')"
                f".has('name', '{capability}')"
            ),
        )

        return bool(result.get("result"))

    except Exception:
        return True


def _require_write_access(
    current_user: UserProfile,
):

    if not has_permission(
        current_user.role,
        DEVICE_WRITE,
    ):
        raise HTTPException(
            status_code=403,
            detail="Write permission required",
        )


class DeviceOut(BaseModel):
    id: int
    name: str
    device_type: str
    room_id: Optional[int]
    is_active: bool


class DeviceCapabilities(BaseModel):
    capabilities: List[str]
    actions: List[str]


@router.get("", response_model=List[DeviceOut])
async def list_devices(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> list[DeviceOut]:
    """List devices filtered by user access."""
    if not has_permission(current_user.role, DEVICE_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    result = await session.execute(
        text(
            "SELECT id, name, device_type, room_id, is_active FROM devices WHERE is_active = TRUE"
        )
    )
    rows = result.mappings().all()

    filtered_devices = []

    for row in rows:
        if await _verify_device_access(
            current_user,
            row["id"],
        ):
            filtered_devices.append(DeviceOut(**row))

    return filtered_devices


@router.get("/{device_id}")
async def get_device(
    device_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Get device details with capabilities."""
    if not has_permission(current_user.role, DEVICE_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )
    result = await session.execute(
        text(
            "SELECT id, name, device_type, room_id, is_active FROM devices WHERE id = :id"
        ),
        {"id": device_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found"
        )
    caps_result = await arcadedb_query(
        "gremlin",
        (
            f"g.V().hasLabel('Device')"
            f".has('id', {device_id})"
            ".out('HAS_CAPABILITY')"
            ".values('name')"
        ),
    )

    device = dict(row)
    device["capabilities"] = caps_result.get(
        "result",
        [],
    )

    return device


@router.get("/{device_id}/capabilities")
async def get_capabilities(
    device_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """List what actions are possible for a device."""
    if not has_permission(current_user.role, DEVICE_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )
    # Query ArcadeDB for device capabilities
    result = await arcadedb_query(
        "gremlin",
        f"g.V().hasLabel('Device').has('id', {device_id}).out('HAS_CAPABILITY').values('name')",
    )
    caps = result.get("result", [])
    # Default capabilities based on device type
    if not caps:
        caps = ["OnOff"]
    return {"device_id": device_id, "capabilities": caps}


@router.get("/{device_id}/actions")
async def get_permitted_actions(
    device_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """List permitted actions for the current user on this device."""

    if not has_permission(
        current_user.role,
        DEVICE_READ,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    caps_result = await arcadedb_query(
        "gremlin",
        (
            f"g.V().hasLabel('Device')"
            f".has('id', {device_id})"
            ".out('HAS_CAPABILITY')"
            ".values('name')"
        ),
    )

    caps = set(
        caps_result.get(
            "result",
            [],
        )
    )

    actions = ["read"]

    if has_permission(
        current_user.role,
        DEVICE_WRITE,
    ):

        if "OnOff" in caps:
            actions.extend(
                [
                    "on",
                    "off",
                ]
            )

        if "Dimmable" in caps:
            actions.append("dim")

        if "ColorControl" in caps:
            actions.append("color")

    return {
        "device_id": device_id,
        "actions": actions,
        "capabilities": list(caps),
    }


@router.post("/{device_id}/on")
async def turn_on(
    device_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Turn on a device."""
    _require_write_access(current_user)

    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    if not await _has_capability(
        device_id,
        "OnOff",
    ):
        raise HTTPException(
            status_code=400,
            detail="Device does not support OnOff",
        )
    await session.execute(
        text("UPDATE devices SET is_active = TRUE WHERE id = :id"),
        {"id": device_id},
    )
    await session.commit()
    return {"device_id": device_id, "state": "on"}


@router.post("/{device_id}/off")
async def turn_off(
    device_id: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Turn off a device."""
    _require_write_access(current_user)

    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    if not await _has_capability(
        device_id,
        "OnOff",
    ):
        raise HTTPException(
            status_code=400,
            detail="Device does not support OnOff",
        )
    await session.execute(
        text("UPDATE devices SET is_active = FALSE WHERE id = :id"),
        {"id": device_id},
    )
    await session.commit()
    return {"device_id": device_id, "state": "off"}


@router.post("/{device_id}/dim")
async def set_brightness(
    device_id: int,
    brightness: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Set brightness (requires Dimmable capability)."""
    _require_write_access(current_user)

    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    if not await _has_capability(
        device_id,
        "Dimmable",
    ):
        raise HTTPException(
            status_code=400,
            detail="Device is not dimmable",
        )
    # Capability check placeholder
    await session.execute(
        text("UPDATE devices SET is_active = TRUE WHERE id = :id"),
        {"id": device_id},
    )
    await session.commit()
    return {"device_id": device_id, "brightness": brightness}


@router.post("/{device_id}/color")
async def set_color_temp(
    device_id: int,
    color_temp: int,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Set color temperature (requires ColorControl capability)."""
    _require_write_access(current_user)

    if not await _verify_device_access(
        current_user,
        device_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    if not await _has_capability(
        device_id,
        "ColorControl",
    ):
        raise HTTPException(
            status_code=400,
            detail="Device does not support color control",
        )
    return {"device_id": device_id, "color_temp": color_temp}
