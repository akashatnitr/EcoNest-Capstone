"""Graph API routes for ArcadeDB operations."""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import (
    arcadedb_query,
    get_mysql_session,
    healthcheck_arcadedb,
)
from orchestrator.core.permissions import USER_ADMIN, has_permission
from orchestrator.graph.builder import ConflictPolicy, incremental_sync
from orchestrator.graph.ha_importer import bootstrap_home_assistant_graph
from orchestrator.graph.queries import (
    get_devices_in_room,
)

router = APIRouter(prefix="/graph", tags=["graph"])


class RoomSummary(BaseModel):
    id: str | None = None
    name: str
    device_count: int


class DevicesResponse(BaseModel):
    room_id: str
    devices: list[dict[str, Any]]


class NeighborsResponse(BaseModel):
    device_id: str
    neighbors: list[dict[str, Any]]


class GremlinQuery(BaseModel):
    query: str = Field(min_length=1, max_length=5000)


class RawQueryResponse(BaseModel):
    result: list[Any]


class GraphSyncRequest(BaseModel):
    last_sync: str = Field(default="1970-01-01 00:00:00")
    conflict_policy: ConflictPolicy = "update"


class GraphSyncResponse(BaseModel):
    changed_rooms: int
    changed_devices: int
    changed_sensor_readings: int
    last_sync: str
    conflict_policy: ConflictPolicy


class HomeAssistantGraphSyncRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1)


class HomeAssistantGraphSyncResponse(BaseModel):
    created_database: bool
    schema_commands: int
    home_assistant_states: int
    registry_loaded: bool
    registry_source: str
    rooms: int
    devices: int
    sensors: int
    observations: int
    skipped_observations: int
    edges: int
    database: str


@router.get("/health")
async def graph_health() -> dict[str, str]:
    """ArcadeDB connectivity check."""
    ok = await healthcheck_arcadedb()
    return {"status": "ok" if ok else "unhealthy", "service": "arcadedb"}


@router.get("/rooms", response_model=list[RoomSummary])
async def list_rooms(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> list[RoomSummary]:
    """List rooms with device counts."""
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Room')"
            ".project('room','count')"
            ".by(valueMap(true))"
            ".by(__.in('LOCATED_IN').hasLabel('Device').count())"
        ),
    )
    rooms: list[RoomSummary] = []
    for item in result.get("result", []):
        if not isinstance(item, dict):
            continue
        room_data = item.get("room", {})
        if not isinstance(room_data, dict):
            continue
        rooms.append(
            RoomSummary(
                id=_first_string(room_data.get("@rid") or room_data.get("id")),
                name=_first_string(room_data.get("name")) or "",
                device_count=int(item.get("count") or 0),
            )
        )
    return rooms


@router.get("/rooms/{room_id}/devices", response_model=DevicesResponse)
async def room_devices(
    room_id: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> DevicesResponse:
    """Devices in a room."""
    devices = await get_devices_in_room(room_id)
    return DevicesResponse(room_id=room_id, devices=devices)


@router.get("/devices/{device_id}/neighbors", response_model=NeighborsResponse)
async def device_neighbors(
    device_id: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> NeighborsResponse:
    """Related devices, circuits, and rooms."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{_escape_gremlin_string(device_id)}').bothE().otherV().valueMap(true)",
    )
    return NeighborsResponse(
        device_id=device_id,
        neighbors=[row for row in result.get("result", []) if isinstance(row, dict)],
    )


@router.post("/query", response_model=RawQueryResponse)
async def raw_query(
    req: GremlinQuery,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> RawQueryResponse:
    """Raw Gremlin query (admin only, with validation)."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    _validate_readonly_gremlin(req.query)
    result = await arcadedb_query("gremlin", req.query)
    return RawQueryResponse(result=result.get("result", []))


@router.post("/sync", response_model=GraphSyncResponse)
async def sync_graph(
    req: GraphSyncRequest,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    mysql_session: Annotated[AsyncSession, Depends(get_mysql_session)],
) -> GraphSyncResponse:
    """Trigger MySQL to ArcadeDB graph sync."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    result = await incremental_sync(
        mysql_session=mysql_session,
        last_sync=req.last_sync,
        conflict_policy=req.conflict_policy,
    )
    return GraphSyncResponse(**result)


@router.post("/home-assistant/sync", response_model=HomeAssistantGraphSyncResponse)
async def sync_home_assistant_graph(
    req: HomeAssistantGraphSyncRequest,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> HomeAssistantGraphSyncResponse:
    """Trigger live Home Assistant inventory/state sync into ArcadeDB."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    result = await bootstrap_home_assistant_graph(limit=req.limit)
    return HomeAssistantGraphSyncResponse(**result)


def _validate_readonly_gremlin(query: str) -> None:
    normalized = query.strip()
    if not normalized.startswith("g."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Gremlin traversals starting with g. are allowed",
        )

    forbidden_patterns = (
        r"\bdrop\s*\(",
        r"\bdelete\b",
        r"\bremove\s*\(",
        r"\btruncate\b",
        r"\baddv\s*\(",
        r"\badde\s*\(",
        r"\bproperty\s*\(",
        r"\bsideeffect\s*\(",
    )
    lower_query = normalized.lower()
    if any(re.search(pattern, lower_query) for pattern in forbidden_patterns):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Destructive or mutating Gremlin queries are not allowed",
        )


def _first_string(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    return str(value)


def _escape_gremlin_string(value: str) -> str:
    """Escape a string for single-quoted Gremlin literals."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
