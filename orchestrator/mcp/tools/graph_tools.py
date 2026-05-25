"""MCP tools for ArcadeDB graph operations."""

from typing import Any

from pydantic import BaseModel

from orchestrator.core.database import arcadedb_query

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


async def query_arcadedb_handler(
    input_data: QueryArcadeDBInput,
) -> list[dict[str, Any]]:
    """Execute a read-only ArcadeDB query."""
    if not _is_readonly_graph_query(input_data.query):
        return [{"error": "Only read-only graph queries are allowed"}]
    result = await arcadedb_query(input_data.language, input_data.query)
    return _result_rows(result)


async def get_device_neighbors_handler(
    input_data: GetDeviceNeighborsInput,
) -> list[dict[str, Any]]:
    """Get neighbors of a device."""
    device_id = _escape_gremlin_string(input_data.device_id)
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{device_id}').bothE().otherV().valueMap(true)",
    )
    return _result_rows(result)
