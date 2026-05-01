"""Tests for MCP server and tools."""

from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.api.auth import get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.main import app


@pytest.fixture(autouse=True)
def override_deps(admin_user):
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_mysql_session] = AsyncMock
    yield
    app.dependency_overrides.clear()


# ------------------------------------------------------------------
# Tool listing
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools(client):
    resp = client.get("/mcp/tools")
    assert resp.status_code == 200
    data = resp.json()
    tool_names = {t["name"] for t in data["tools"]}
    assert "query_mysql" in tool_names
    assert "ha_get_state" in tool_names
    assert "device_turn_on" in tool_names


# ------------------------------------------------------------------
# Tool invocation
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_invoke_query_mysql(client):
    from pydantic import BaseModel

    class QueryInput(BaseModel):
        sql: str

    with patch(
        "orchestrator.mcp.server._tool_registry",
        {
            "query_mysql": {
                "description": "Test",
                "input_schema": QueryInput,
                "handler": AsyncMock(return_value=[{"id": 1}]),
                "permissions": ["device:read"],
            }
        },
    ):
        resp = client.post(
            "/mcp/tools/query_mysql",
            json={"name": "query_mysql", "arguments": {"sql": "SELECT 1"}},
        )
    assert resp.status_code == 200
    assert resp.json()["result"] == [{"id": 1}]


@pytest.mark.anyio
async def test_invoke_unknown_tool(client):
    resp = client.post(
        "/mcp/tools/nonexistent",
        json={"name": "nonexistent", "arguments": {}},
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Resources
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_resource_snapshot(client):
    resp = client.get("/mcp/resources/home://snapshot")
    assert resp.status_code == 200
    assert resp.json()["type"] == "snapshot"


@pytest.mark.anyio
async def test_get_resource_devices(client):
    resp = client.get("/mcp/resources/home://devices")
    assert resp.status_code == 200
    assert resp.json()["type"] == "devices"


@pytest.mark.anyio
async def test_get_resource_not_found(client):
    resp = client.get("/mcp/resources/home://unknown")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_prompt_energy_review(client):
    resp = client.get("/mcp/prompts/energy_review")
    assert resp.status_code == 200
    assert "energy" in resp.json()["text"].lower()


@pytest.mark.anyio
async def test_get_prompt_not_found(client):
    resp = client.get("/mcp/prompts/unknown_prompt")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Permission boundaries
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools_guest_limited(client, guest_user):
    app.dependency_overrides[get_current_user] = lambda: guest_user
    resp = client.get("/mcp/tools")
    assert resp.status_code == 200
    tool_names = {t["name"] for t in resp.json()["tools"]}
    # Guest should not see write tools
    assert "device_turn_on" not in tool_names
