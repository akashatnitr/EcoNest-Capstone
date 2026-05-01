"""Graph API routes for ArcadeDB operations."""

from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import arcadedb_query, healthcheck_arcadedb
from orchestrator.core.permissions import USER_ADMIN, has_permission
from orchestrator.graph.queries import (
    get_devices_in_room,
)

router = APIRouter(prefix="/graph", tags=["graph"])


class RoomSummary(BaseModel):
    name: str
    device_count: int


class GremlinQuery(BaseModel):
    query: str


@router.get("/health")
async def graph_health():
    """ArcadeDB connectivity check."""
    ok = await healthcheck_arcadedb()
    return {"status": "ok" if ok else "unhealthy", "service": "arcadedb"}


@router.get("/rooms", response_model=List[RoomSummary])
async def list_rooms(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """List rooms with device counts."""
    result = await arcadedb_query(
        "gremlin",
        "g.V().hasLabel('Room').as('room').in('LOCATED_IN').count().as('count').select('room','count')",
    )
    rooms = []
    for item in result.get("result", []):
        room_data = item.get("room", {})
        count = item.get("count", 0)
        rooms.append(RoomSummary(name=room_data.get("name", ""), device_count=count))
    return rooms


@router.get("/rooms/{room_id}/devices")
async def room_devices(
    room_id: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Devices in a room."""
    devices = await get_devices_in_room(room_id)
    return {"room_id": room_id, "devices": devices}


@router.get("/devices/{device_id}/neighbors")
async def device_neighbors(
    device_id: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Related devices, circuits, and rooms."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{device_id}').bothE().otherV().valueMap()",
    )
    return {"device_id": device_id, "neighbors": result.get("result", [])}


@router.post("/query")
async def raw_query(
    req: GremlinQuery,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Raw Gremlin query (admin only, with validation)."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    # Basic validation: reject destructive commands
    forbidden = {"drop", "delete", "remove", "truncate"}
    lower_q = req.query.lower()
    if any(word in lower_q for word in forbidden):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Destructive queries are not allowed",
        )
    result = await arcadedb_query("gremlin", req.query)
    return {"result": result.get("result", [])}
