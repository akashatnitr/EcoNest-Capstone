"""MCP tools for MySQL database operations."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from orchestrator.core.database import mysql_session_context

READ_ONLY_SQL_COMMANDS = {"select", "show", "describe", "explain"}


class QueryMySQLInput(BaseModel):
    sql: str


class GetReadingsInput(BaseModel):
    device_id: int
    limit: int = 10


def _normalize_readonly_sql(sql: str) -> str | None:
    """Return normalized SQL when it is a single read-only statement."""
    normalized = sql.strip().rstrip(";").strip()
    if not normalized:
        return None

    first_word = normalized.split(maxsplit=1)[0].lower()
    if first_word not in READ_ONLY_SQL_COMMANDS:
        return None
    if ";" in normalized:
        return None
    return normalized


async def query_mysql_handler(input_data: QueryMySQLInput) -> list[dict[str, Any]]:
    """Execute a read-only SQL query."""
    sql = _normalize_readonly_sql(input_data.sql)
    if sql is None:
        return [{"error": "Only single read-only SQL statements are allowed"}]

    async with mysql_session_context() as session:
        result = await session.execute(text(sql))
        rows = result.mappings().all()
        return [dict(row) for row in rows]


async def get_readings_handler(input_data: GetReadingsInput) -> list[dict[str, Any]]:
    """Get recent sensor readings for a device."""
    async with mysql_session_context() as session:
        result = await session.execute(
            text(
                "SELECT * FROM sensor_readings WHERE device_id = :device_id "
                "ORDER BY timestamp DESC LIMIT :limit"
            ),
            {"device_id": input_data.device_id, "limit": input_data.limit},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]
