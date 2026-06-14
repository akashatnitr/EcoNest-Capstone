"""Sensor reading ingestion API."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.core.permissions import DEVICE_WRITE, has_permission

router = APIRouter(prefix="/readings", tags=["readings"])


class SensorReadingIn(BaseModel):
    device_id: int
    data: dict[str, Any] = Field(default_factory=dict)


class AddReadingsResponse(BaseModel):
    message: str
    total_submitted: int
    inserted: int
    errors: list[str] | None = None


@router.post(
    "/add",
    response_model=AddReadingsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_readings(
    readings: SensorReadingIn | list[SensorReadingIn],
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> AddReadingsResponse:
    """Insert one or more sensor readings into MySQL."""
    if not has_permission(current_user.role, DEVICE_WRITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="device:write permission required",
        )

    batch = readings if isinstance(readings, list) else [readings]
    inserted = 0
    errors: list[str] = []

    try:
        for index, reading in enumerate(batch, start=1):
            if not reading.data:
                errors.append(f"Reading {index} missing data")
                continue

            device = await session.execute(
                text(
                    """
                    SELECT room_id, device_type, is_active
                    FROM devices
                    WHERE id = :device_id
                    """
                ),
                {"device_id": reading.device_id},
            )
            row = device.mappings().first()
            if row is None:
                errors.append(
                    f"Reading {index} has invalid device_id {reading.device_id}"
                )
                continue
            if not row["is_active"]:
                errors.append(
                    f"Reading {index} device {reading.device_id} is not active"
                )
                continue
            if row["room_id"] is None:
                errors.append(f"Reading {index} device {reading.device_id} has no room")
                continue

            await session.execute(
                text(
                    """
                    INSERT INTO sensor_readings (device_id, room_id, data)
                    VALUES (:device_id, :room_id, :data)
                    """
                ),
                {
                    "device_id": reading.device_id,
                    "room_id": row["room_id"],
                    "data": json.dumps(reading.data),
                },
            )
            inserted += 1

        if inserted == 0:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "No readings inserted",
                    "total_submitted": len(batch),
                    "inserted": inserted,
                    "errors": errors,
                },
            )

        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return AddReadingsResponse(
        message=f"Inserted {inserted} reading(s)",
        total_submitted=len(batch),
        inserted=inserted,
        errors=errors or None,
    )
