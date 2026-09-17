"""MCP tools for ArcadeDB graph operations."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from orchestrator.core.database import arcadedb_query
from orchestrator.mcp.models import ToolExecutionResult

READ_ONLY_GRAPH_PREFIXES = {"g.", "select", "match", "traverse"}
GRAPH_MUTATION_TOKENS = {
    ".addE(",
    ".addV(",
    ".drop(",
    ".property(",
    ".remove(",
    " create ",
    " delete ",
    " drop ",
    " insert ",
    " update ",
    " upsert ",
}


class QueryArcadeDBInput(BaseModel):
    query: str
    language: str = "gremlin"


class GetDeviceNeighborsInput(BaseModel):
    device_id: str


class RecordDeviceActionInput(BaseModel):
    """Audit information for one device-control attempt."""

    action: str
    task_id: str = ""
    user_id: str = ""
    success: bool


def _result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("result", [])
    if not isinstance(rows, list):
        return [{"result": rows}]
    return [row if isinstance(row, dict) else {"result": row} for row in rows]


def _is_readonly_graph_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    padded_lower = f" {normalized.lower()} "
    if any(token.lower() in padded_lower for token in GRAPH_MUTATION_TOKENS):
        return False
    first_word = normalized.split(maxsplit=1)[0].lower()
    return any(first_word.startswith(prefix) for prefix in READ_ONLY_GRAPH_PREFIXES)


def _escape_gremlin_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _sql_timestamp() -> str:
    """Return a UTC timestamp literal accepted by ArcadeDB SQL."""
    value = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return f"'{_escape_gremlin_string(value)}'"


async def query_arcadedb_handler(
    input_data: QueryArcadeDBInput,
) -> list[dict[str, Any]]:
    """Execute a read-only ArcadeDB query."""
    if not _is_readonly_graph_query(input_data.query):
        return ToolExecutionResult(
            success=False,
            capability="query_arcadedb",
            warnings=["Only read-only graph queries are allowed"],
        )
    result = await arcadedb_query(input_data.language, input_data.query)
    rows = _result_rows(result)
    return ToolExecutionResult(
        capability="query_arcadedb",
        result=rows,
        metadata={
            "language": input_data.language,
            "row_count": len(rows),
            "readonly": True,
        },
    )


async def get_device_neighbors_handler(
    input_data: GetDeviceNeighborsInput,
) -> list[dict[str, Any]]:
    """Get neighbors of a device."""
    device_id = _escape_gremlin_string(input_data.device_id)
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{device_id}').bothE().otherV().valueMap(true)",
    )
    rows = _result_rows(result)
    return ToolExecutionResult(
        capability="get_device_neighbors",
        result=rows,
        metadata={
            "device_id": input_data.device_id,
            "neighbor_count": len(rows),
        },
    )


async def record_device_action_handler(
    input_data: RecordDeviceActionInput,
) -> ToolExecutionResult:
    """Record a device-control outcome apart from the Action catalog."""
    await _ensure_action_execution_schema()
    command = (
        "CREATE VERTEX ActionExecution "
        f"SET action = '{_escape_gremlin_string(input_data.action)}', "
        f"task_id = '{_escape_gremlin_string(input_data.task_id)}', "
        f"user_id = '{_escape_gremlin_string(input_data.user_id)}', "
        f"success = {str(input_data.success).lower()}, "
        f"timestamp = {_sql_timestamp()}"
    )
    await arcadedb_query("sql", command, readonly=False)
    return ToolExecutionResult(
        capability="record_device_action",
        result={"recorded": True},
        metadata={"task_id": input_data.task_id},
    )


async def _ensure_action_execution_schema() -> None:
    """Create the runtime audit vertex type for existing graph deployments."""
    for command in (
        "CREATE VERTEX TYPE ActionExecution IF NOT EXISTS",
        "CREATE PROPERTY ActionExecution.action IF NOT EXISTS STRING",
        "CREATE PROPERTY ActionExecution.task_id IF NOT EXISTS STRING",
        "CREATE PROPERTY ActionExecution.user_id IF NOT EXISTS STRING",
        "CREATE PROPERTY ActionExecution.success IF NOT EXISTS BOOLEAN",
        "CREATE PROPERTY ActionExecution.timestamp IF NOT EXISTS DATETIME",
        "CREATE INDEX IF NOT EXISTS ON ActionExecution(task_id) NOTUNIQUE",
    ):
        await arcadedb_query("sql", command, readonly=False)
