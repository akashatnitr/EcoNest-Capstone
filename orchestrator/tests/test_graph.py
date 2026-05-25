"""Tests for the graph layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.graph.builder import (
    grant_access_to_graph,
    incremental_sync,
    sync_sensor_readings_to_graph,
)
from orchestrator.graph.models import (
    Action,
    Capability,
    Device,
    HasAccess,
    Home,
    HomeAssistantDomain,
    PermissionName,
    Room,
    User,
    device_type_for_ha_domain,
)
from orchestrator.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_user():
    return UserProfile(
        id=1,
        email="admin@example.com",
        role="superadmin",
        household_id=None,
        is_active=True,
    )


@pytest.fixture
def guest_user():
    return UserProfile(
        id=2, email="guest@example.com", role="guest", household_id=None, is_active=True
    )


@pytest.fixture(autouse=True)
def override_deps(admin_user):
    """Override auth and db dependencies for all graph tests."""
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_mysql_session] = AsyncMock
    yield
    app.dependency_overrides.clear()


def _mock_arcadedb_result(result_data: dict):
    """Helper to patch arcadedb_query in api.graph module."""
    return patch(
        "orchestrator.api.graph.arcadedb_query",
        new=AsyncMock(return_value=result_data),
    )


def _mock_queries_arcadedb(result_data: dict):
    """Helper to patch arcadedb_query in graph.queries module."""
    return patch(
        "orchestrator.graph.queries.arcadedb_query",
        new=AsyncMock(return_value=result_data),
    )


def _mock_mysql_row(row: dict | None):
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result


def _mock_mysql_rows(rows: list[dict]):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


def _device_row(**overrides):
    row = {
        "id": 99,
        "name": "Kitchen Light",
        "device_type": "energy",
        "room_id": 10,
        "room_name": "Kitchen",
        "is_active": True,
    }
    row.update(overrides)
    return row


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


def test_graph_vertex_models_validate_known_types():
    home = Home(name="Professor's House", home_assistant_url="http://localhost:8123")
    room = Room(name="Kitchen", room_type="Kitchen", ha_area_id="kitchen")
    device = Device(
        name="Washer",
        device_type="SmartPlug",
        ha_entity_id="sensor.washer_machine_state",
        manufacturer="Kasa",
    )
    user = User(email="owner@example.com", role="homeowner", household_id=1)
    capability = Capability(name="PowerMonitoring")
    action = Action(name="ReadState")

    assert home.name == "Professor's House"
    assert room.room_type == "Kitchen"
    assert device.device_type == "SmartPlug"
    assert user.role == "homeowner"
    assert capability.name == "PowerMonitoring"
    assert action.parameters == {}


def test_graph_room_rejects_unknown_room_type():
    with pytest.raises(ValidationError):
        Room(name="Mystery", room_type="Attic")


def test_graph_device_rejects_unknown_device_type():
    with pytest.raises(ValidationError):
        Device(name="Mystery Device", device_type="unsupported")


def test_graph_device_accepts_legacy_mysql_energy_type_target():
    device = Device(name="Balance", device_type="EnergyMonitor")

    assert device.device_type == "EnergyMonitor"


def test_graph_models_cover_home_assistant_registry_domains():
    observed_domains = {
        "automation",
        "binary_sensor",
        "button",
        "climate",
        "cover",
        "device_tracker",
        "event",
        "fan",
        "input_boolean",
        "light",
        "media_player",
        "number",
        "person",
        "select",
        "sensor",
        "switch",
        "todo",
        "tts",
        "update",
        "valve",
        "weather",
    }

    assert observed_domains == {domain.value for domain in HomeAssistantDomain}


def test_graph_device_accepts_home_assistant_domain_metadata():
    device = Device(
        name="Living Room Light",
        device_type=device_type_for_ha_domain("light"),
        ha_domain="light",
        ha_entity_id="light.living_room",
        ha_area_id="living_room",
        ha_device_id="abc123",
    )

    assert device.device_type == "SmartBulb"
    assert device.ha_domain == "light"


def test_graph_device_rejects_mismatched_home_assistant_domain():
    with pytest.raises(ValidationError):
        Device(
            name="Bad Registry Row",
            device_type="SmartBulb",
            ha_domain="switch",
            ha_entity_id="light.bad_registry_row",
        )


def test_graph_device_type_helper_falls_back_for_unknown_domain():
    assert device_type_for_ha_domain("unknown_domain") == "Other"


def test_graph_edge_accepts_arcadedb_endpoint_aliases():
    edge = HasAccess(
        **{
            "from": "#1:0",
            "to": "#2:0",
            "permission": PermissionName.ROOM_READ,
            "allowed_start_hour": 8,
            "allowed_end_hour": 20,
        }
    )

    assert edge.from_id == "#1:0"
    assert edge.to_id == "#2:0"
    assert edge.permission == "room:read"
    assert edge.model_dump(by_alias=True)["from"] == "#1:0"


def test_graph_access_edge_requires_complete_time_window():
    with pytest.raises(ValidationError):
        HasAccess(
            **{
                "from": "#1:0",
                "to": "#2:0",
                "permission": "room:read",
                "allowed_start_hour": 8,
            }
        )


def test_graph_action_rejects_empty_parameter_names():
    with pytest.raises(ValidationError):
        Action(name="SetBrightness", parameters={"": 80})


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_health(client):
    with patch(
        "orchestrator.api.graph.healthcheck_arcadedb",
        new=AsyncMock(return_value=True),
    ):
        resp = client.get("/graph/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ------------------------------------------------------------------
# Rooms
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_rooms(client):
    with _mock_arcadedb_result(
        {
            "result": [
                {"room": {"name": "Kitchen"}, "count": 3},
                {"room": {"name": "Garage"}, "count": 1},
            ]
        }
    ):
        resp = client.get("/graph/rooms")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "Kitchen"
    assert data[0]["device_count"] == 3


# ------------------------------------------------------------------
# Room devices
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_room_devices(client):
    with _mock_queries_arcadedb(
        {"result": [{"name": ["Washer"], "device_type": ["SmartPlug"]}]}
    ):
        resp = client.get("/graph/rooms/%231:0/devices")
    assert resp.status_code == 200
    assert resp.json()["devices"][0]["name"] == ["Washer"]


# ------------------------------------------------------------------
# Device neighbors
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_device_neighbors(client):
    with _mock_arcadedb_result({"result": [{"name": ["Kitchen"]}]}):
        resp = client.get("/graph/devices/%232:0/neighbors")
    assert resp.status_code == 200
    assert len(resp.json()["neighbors"]) == 1


# ------------------------------------------------------------------
# Raw query (admin only)
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_query_admin(client):
    with _mock_arcadedb_result({"result": [{"name": ["Test"]}]}):
        resp = client.post(
            "/graph/query",
            json={"query": "g.V().hasLabel('Room').valueMap()"},
        )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_raw_query_forbidden_words(client):
    resp = client.post(
        "/graph/query",
        json={"query": "g.V().drop()"},
    )
    assert resp.status_code == 400
    assert "Destructive" in resp.json()["detail"]


@pytest.mark.anyio
async def test_raw_query_non_admin(client, guest_user):
    app.dependency_overrides[get_current_user] = lambda: guest_user
    resp = client.post(
        "/graph/query",
        json={"query": "g.V().valueMap()"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_grant_access_to_graph_creates_room_edge():
    session = AsyncMock()
    session.execute.side_effect = [
        _mock_mysql_row({"email": "user@example.com"}),
        _mock_mysql_row({"name": "Kitchen"}),
    ]

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        await grant_access_to_graph(
            mysql_session=session,
            user_id=7,
            room_id=10,
            permission="room:read",
            allowed_start_hour=8,
            allowed_end_hour=20,
        )

    query.assert_awaited_once()
    command = query.await_args.args[1]
    assert "CREATE EDGE HAS_ACCESS" in command
    assert "FROM (SELECT FROM User WHERE email = 'user@example.com')" in command
    assert "TO (SELECT FROM Room WHERE name = 'Kitchen')" in command
    assert "permission = 'room:read'" in command
    assert "allowed_start_hour = 8" in command
    assert "allowed_end_hour = 20" in command


@pytest.mark.anyio
async def test_incremental_sync_upserts_rooms_and_devices():
    session = AsyncMock()
    session.execute.side_effect = [
        _mock_mysql_rows(
            [
                {
                    "id": 10,
                    "name": "Kitchen",
                }
            ],
        ),
        _mock_mysql_rows([_device_row()]),
        _mock_mysql_rows(
            [
                {
                    "id": 500,
                    "device_id": 99,
                    "room_id": 10,
                    "timestamp": "2026-05-24 15:00:00",
                    "data": {"power": 12.5},
                }
            ],
        ),
    ]

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        result = await incremental_sync(session)

    assert result["changed_rooms"] == 1
    assert result["changed_devices"] == 1
    assert result["changed_sensor_readings"] == 1
    assert result["conflict_policy"] == "update"
    commands = [call.args[1] for call in query.await_args_list]
    assert commands[0] == "BEGIN"
    assert commands[-1] == "COMMIT"
    assert any(
        "UPDATE Room SET" in command and "UPSERT WHERE mysql_id = 10" in command
        for command in commands
    )
    assert any(
        "UPDATE Device SET" in command
        and "EnergyMonitor" in command
        and "ha_domain = null" in command
        for command in commands
    )
    assert any("CREATE EDGE LOCATED_IN" in command for command in commands)
    assert any("UPDATE SensorReading SET" in command for command in commands)


@pytest.mark.anyio
async def test_incremental_sync_uses_skip_conflict_policy():
    session = AsyncMock()
    session.execute.side_effect = [
        _mock_mysql_rows(
            [
                {
                    "id": 10,
                    "name": "Kitchen",
                }
            ],
        ),
        _mock_mysql_rows([_device_row()]),
        _mock_mysql_rows([]),
    ]

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        result = await incremental_sync(session, conflict_policy="skip")

    assert result["conflict_policy"] == "skip"
    commands = [call.args[1] for call in query.await_args_list]
    assert any("CREATE VERTEX Room SET" in command for command in commands)
    assert any("IF NOT EXISTS WHERE mysql_id = 10" in command for command in commands)


@pytest.mark.anyio
async def test_sync_sensor_readings_filters_by_last_sync():
    session = AsyncMock()
    session.execute.return_value = _mock_mysql_rows(
        [
            {
                "id": 501,
                "device_id": 99,
                "room_id": 10,
                "timestamp": "2026-05-24 15:05:00",
                "data": '{"motion": true}',
            }
        ]
    )

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        result = await sync_sensor_readings_to_graph(
            session,
            last_sync="2026-05-24 15:00:00",
        )

    assert result["changed_sensor_readings"] == 1
    session.execute.assert_awaited_once()
    assert session.execute.await_args.args[1] == {"last_sync": "2026-05-24 15:00:00"}
    commands = [call.args[1] for call in query.await_args_list]
    assert any("UPDATE SensorReading SET" in command for command in commands)
    assert any('"motion": true' in command for command in commands)


@pytest.mark.anyio
async def test_incremental_sync_rolls_back_on_arcadedb_error():
    session = AsyncMock()
    session.execute.side_effect = [
        _mock_mysql_rows(
            [
                {
                    "id": 10,
                    "name": "Kitchen",
                }
            ],
        ),
        _mock_mysql_rows([]),
        _mock_mysql_rows([]),
    ]

    async def failing_query(language, command, readonly=True):
        if command.startswith("UPDATE Room"):
            raise RuntimeError("arcadedb failed")
        return {"result": []}

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(side_effect=failing_query),
    ) as query:
        with pytest.raises(RuntimeError):
            await incremental_sync(session)

    commands = [call.args[1] for call in query.await_args_list]
    assert commands[0] == "BEGIN"
    assert commands[-1] == "ROLLBACK"
