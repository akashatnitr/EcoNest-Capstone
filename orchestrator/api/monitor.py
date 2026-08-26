"""Read-only MySQL data explorer for trusted Tailnet users."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.database import get_mysql_session

router = APIRouter(prefix="/monitor", tags=["monitor"])

TABLE_QUERIES = {
    "sensor_readings": """
        SELECT sr.id, sr.timestamp, r.name AS room, d.name AS device,
               d.ha_entity_id, sr.data
        FROM sensor_readings sr
        JOIN devices d ON d.id = sr.device_id
        JOIN rooms r ON r.id = sr.room_id
        ORDER BY sr.timestamp DESC, sr.id DESC
        LIMIT :limit
    """,
    "devices": """
        SELECT id, name, device_type, room_id, ha_entity_id, is_active
        FROM devices
        ORDER BY name ASC
        LIMIT :limit
    """,
    "rooms": """
        SELECT r.id, r.name, r.description, r.ha_area_id, h.name AS household
        FROM rooms r
        LEFT JOIN households h ON h.id = r.household_id
        ORDER BY r.name ASC
        LIMIT :limit
    """,
    "home_snapshot": """
        SELECT id, room_id, occupancy_estimate, active_devices, power_trend,
               sound_spike, updated_at
        FROM home_snapshot
        ORDER BY updated_at DESC
        LIMIT :limit
    """,
    "home_analytics": """
        SELECT id, room_id, hour_of_day, motion_probability,
               avg_power_this_hour, total_kwh, baseline_sound_level, computed_at
        FROM home_analytics
        ORDER BY computed_at DESC
        LIMIT :limit
    """,
    "audit_events": """
        SELECT id, event_time, event_type, task_id, agent, success, source
        FROM audit_events
        ORDER BY event_time DESC
        LIMIT :limit
    """,
}

FORBIDDEN_SQL = re.compile(
    r"\b("
    r"alter|analyze|benchmark|call|create|delete|describe|do|drop|grant|"
    r"insert|kill|load_file|lock|optimize|outfile|replace|revoke|set|show|"
    r"sleep|truncate|unlock|update|use"
    r")\b|--|/\*|\*/|;",
    flags=re.IGNORECASE,
)
FROM_OR_JOIN = re.compile(r"\b(?:from|join)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?", re.IGNORECASE)


class SqlQuery(BaseModel):
    """A single read-only SQL statement submitted from the monitor page."""

    query: str = Field(min_length=8, max_length=5000)


@router.get("", response_class=HTMLResponse)
async def monitor_page() -> HTMLResponse:
    """Serve the small read-only MySQL explorer."""
    page = Path(__file__).resolve().parents[1] / "static" / "monitor.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/api/summary")
async def summary(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Return small, useful MySQL data-health totals."""
    result = await session.execute(
        text(
            """
            SELECT
                (SELECT COUNT(*) FROM sensor_readings) AS reading_count,
                (SELECT MAX(timestamp) FROM sensor_readings) AS newest_reading,
                (SELECT COUNT(*) FROM sensor_readings
                 WHERE timestamp >= NOW() - INTERVAL 1 HOUR) AS readings_last_hour,
                (SELECT COUNT(*) FROM devices WHERE is_active = TRUE) AS active_devices,
                (SELECT COUNT(*) FROM rooms) AS room_count
            """
        )
    )
    row = result.mappings().one()
    return jsonable_encoder(dict(row))


@router.get("/api/readings")
async def readings(
    session: AsyncSession = Depends(get_mysql_session),
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
) -> dict[str, list[dict[str, Any]]]:
    """Return the newest sensor readings without requiring a SQL query."""
    rows = await _rows(session, TABLE_QUERIES["sensor_readings"], {"limit": limit})
    return {"rows": rows}


@router.get("/api/tables")
async def tables(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, list[dict[str, Any]]]:
    """List the safe, browseable tables and their row counts."""
    output: list[dict[str, Any]] = []
    for table_name in TABLE_QUERIES:
        result = await session.execute(text(f"SELECT COUNT(*) AS count FROM {table_name}"))
        output.append({"name": table_name, "count": int(result.scalar_one())})
    return {"tables": output}


@router.get("/api/tables/{table_name}")
async def table_rows(
    table_name: str,
    session: AsyncSession = Depends(get_mysql_session),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Browse one allowlisted MySQL table with a capped result size."""
    query = TABLE_QUERIES.get(table_name)
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown table")
    return {"rows": await _rows(session, query, {"limit": limit})}


@router.post("/api/query")
async def run_query(
    request: SqlQuery,
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, list[dict[str, Any]]]:
    """Run one bounded, read-only SELECT query for the trusted monitor user."""
    query = request.query.strip()
    queried_tables = {
        match.group(1).lower() for match in FROM_OR_JOIN.finditer(query)
    }
    if (
        not query.lower().startswith("select ")
        or FORBIDDEN_SQL.search(query)
        or not queried_tables.issubset(TABLE_QUERIES)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only one read-only SELECT query against safe monitor tables is allowed",
        )
    bounded_query = f"SELECT * FROM ({query}) AS monitor_query LIMIT 200"
    return {"rows": await _rows(session, bounded_query, {})}


async def _rows(
    session: AsyncSession,
    query: str,
    values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute an approved read query and make JSON database values browser-safe."""
    result = await session.execute(text(query), values)
    rows = [dict(row) for row in result.mappings().all()]
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str) and key in {"data", "active_devices"}:
                try:
                    row[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
            elif isinstance(value, datetime):
                row[key] = value.isoformat()
    return jsonable_encoder(rows)
