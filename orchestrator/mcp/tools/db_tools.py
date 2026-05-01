"""MCP tools for MySQL database operations."""

from pydantic import BaseModel
from sqlalchemy import text

from orchestrator.core.database import get_mysql_session


class QueryMySQLInput(BaseModel):
    sql: str


class GetReadingsInput(BaseModel):
    device_id: int
    limit: int = 10


async def query_mysql_handler(input_data: QueryMySQLInput) -> list[dict]:
    """Execute a read-only SQL query."""
    # Safety: reject write operations
    forbidden = {"insert", "update", "delete", "drop", "create", "alter"}
    first_word = input_data.sql.strip().split()[0].lower()
    if first_word in forbidden:
        return [{"error": "Write operations are not allowed"}]

    session_gen = get_mysql_session()
    session = await session_gen.asend(None)
    try:
        result = await session.execute(text(input_data.sql))
        rows = result.mappings().all()
        return [dict(row) for row in rows]
    finally:
        await session_gen.aclose()


async def get_readings_handler(input_data: GetReadingsInput) -> list[dict]:
    """Get recent sensor readings for a device."""
    session_gen = get_mysql_session()
    session = await session_gen.asend(None)
    try:
        result = await session.execute(
            text(
                "SELECT * FROM sensor_readings WHERE device_id = :device_id "
                "ORDER BY timestamp DESC LIMIT :limit"
            ),
            {"device_id": input_data.device_id, "limit": input_data.limit},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]
    finally:
        await session_gen.aclose()
