"""MCP tools for ArcadeDB graph operations."""

from pydantic import BaseModel

from orchestrator.core.database import arcadedb_query


class QueryArcadeDBInput(BaseModel):
    query: str
    language: str = "gremlin"


class GetDeviceNeighborsInput(BaseModel):
    device_id: str


async def query_arcadedb_handler(input_data: QueryArcadeDBInput) -> list[dict]:
    """Execute a read-only ArcadeDB query."""
    forbidden = {"drop", "delete", "remove", "truncate"}
    lower_q = input_data.query.lower()
    if any(word in lower_q for word in forbidden):
        return [{"error": "Destructive queries are not allowed"}]
    result = await arcadedb_query(input_data.language, input_data.query)
    return result.get("result", [])


async def get_device_neighbors_handler(
    input_data: GetDeviceNeighborsInput,
) -> list[dict]:
    """Get neighbors of a device."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{input_data.device_id}').bothE().otherV().valueMap()",
    )
    return result.get("result", [])
