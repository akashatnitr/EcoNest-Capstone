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
    sync_devices_to_graph,
    sync_rooms_to_graph,
    sync_sensor_readings_to_graph,
)
from orchestrator.graph.ha_importer import (
    _home_assistant_websocket_url,
    _is_reasoning_relevant_state,
)
from orchestrator.graph.models import (
    Action,
    CanPerform,
    Capability,
    Contains,
    DependsOn,
    Device,
    HasAccess,
    HasCapability,
    Home,
    HomeAssistantDomain,
    LocatedIn,
    Monitors,
    Owns,
    PermissionName,
    PoweredBy,
    RequiresCapability,
    Room,
    User,
    device_type_for_ha_domain,
)
from orchestrator.graph.queries import (
    get_affected_rooms,
    get_circuit_devices,
    get_devices_in_room,
    get_room_power_consumption,
    get_room_sensor_confidence,
    get_sensor_coverage,
    get_user_accessible_devices,
)
from orchestrator.graph.relationships import sync_graph_relationships
from orchestrator.graph.seeds import (
    GraphSeedInventory,
    SeedDevice,
    SeedRoom,
    build_inventory_from_records,
    default_seed_inventory,
    seed_graph,
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


def _mock_builder_sync(result_data: dict):
    """Helper to patch incremental graph sync in api.graph module."""
    return patch(
        "orchestrator.api.graph.incremental_sync",
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


def _mock_arcadedb_rid(rid: str):
    return {"result": [{"@rid": rid}]}


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


def test_graph_edge_models_validate_relationship_payloads():
    edge_types = [
        Contains,
        PoweredBy,
        Monitors,
        Owns,
        CanPerform,
        HasCapability,
        RequiresCapability,
        DependsOn,
        LocatedIn,
    ]

    for edge_type in edge_types:
        edge = edge_type(**{"from": "#1:0", "to": "#2:0"})
        assert edge.from_id == "#1:0"
        assert edge.to_id == "#2:0"


def test_graph_monitors_accepts_confidence_score():
    edge = Monitors(
        **{
            "from": "#1:0",
            "to": "#2:0",
            "confidence_score": 0.82,
        }
    )

    assert edge.confidence_score == 0.82


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


def test_graph_model_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        Room(name="Kitchen", room_type="Kitchen", unexpected="value")


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
                {"room": {"@rid": "#1:0", "name": ["Kitchen"]}, "count": 3},
                {"room": {"@rid": "#1:1", "name": ["Garage"]}, "count": 1},
            ]
        }
    ) as query:
        resp = client.get("/graph/rooms")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == "#1:0"
    assert data[0]["name"] == "Kitchen"
    assert data[0]["device_count"] == 3
    command = query.await_args.args[1]
    assert ".project('room','count')" in command
    assert ".by(__.in('LOCATED_IN').hasLabel('Device').count())" in command


# ------------------------------------------------------------------
# Room devices
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_room_devices(client):
    with _mock_queries_arcadedb(
        {"result": [{"name": ["Washer"], "device_type": ["SmartPlug"]}]}
    ) as query:
        resp = client.get("/graph/rooms/%231:0/devices")
    assert resp.status_code == 200
    assert resp.json()["devices"][0]["name"] == ["Washer"]
    command = query.await_args.args[1]
    assert "in('LOCATED_IN')" in command
    assert "out('CONTAINS')" in command
    assert ".valueMap(true)" in command


@pytest.mark.anyio
async def test_get_devices_in_room_escapes_rid_literals():
    with _mock_queries_arcadedb({"result": []}) as query:
        await get_devices_in_room("#1:'bad")

    command = query.await_args.args[1]
    assert "#1:\\'bad" in command


@pytest.mark.anyio
async def test_get_room_power_consumption_uses_synced_sensor_readings():
    with _mock_queries_arcadedb({"result": [42.5]}) as query:
        total = await get_room_power_consumption("#1:0")

    assert total == 42.5
    command = query.await_args.args[1]
    assert ".hasLabel('SensorReading')" in command
    assert ".where(eq('room')).by('room_id').by('mysql_id')" in command
    assert ".values('data').select('power').sum()" in command
    assert "power_usage" not in command


@pytest.mark.anyio
async def test_get_room_power_consumption_defaults_empty_result_to_zero():
    with _mock_queries_arcadedb({"result": []}):
        total = await get_room_power_consumption("#1:0")

    assert total == 0.0


@pytest.mark.anyio
async def test_get_user_accessible_devices_uses_room_and_home_paths():
    with _mock_queries_arcadedb({"result": [{"name": ["Washer"]}]}) as query:
        devices = await get_user_accessible_devices("#9:0")

    assert devices == [{"name": ["Washer"]}]
    command = query.await_args.args[1]
    assert "out('HAS_ACCESS').hasLabel('Device')" in command
    assert "out('HAS_ACCESS').hasLabel('Room').in('LOCATED_IN')" in command
    assert "out('HAS_ACCESS').hasLabel('Room').out('CONTAINS')" in command
    assert "out('OWNS').out('CONTAINS').hasLabel('Room').in('LOCATED_IN')" in command
    assert "out('OWNS').out('CONTAINS').hasLabel('Room').out('CONTAINS')" in command
    assert ".dedup().valueMap(true)" in command


@pytest.mark.anyio
async def test_get_sensor_coverage_uses_monitors_edge():
    with _mock_queries_arcadedb({"result": [{"name": ["Motion Sensor"]}]}) as query:
        sensors = await get_sensor_coverage("#4:0")

    assert sensors == [{"name": ["Motion Sensor"]}]
    command = query.await_args.args[1]
    assert ".in('MONITORS')" in command
    assert ".valueMap(true)" in command


@pytest.mark.anyio
async def test_get_circuit_devices_uses_powered_by_edge():
    with _mock_queries_arcadedb({"result": [{"name": ["Oven Breaker"]}]}) as query:
        devices = await get_circuit_devices("#3:0")

    assert devices == [{"name": ["Oven Breaker"]}]
    command = query.await_args.args[1]
    assert ".in('POWERED_BY')" in command
    assert ".hasLabel('Device')" in command
    assert ".valueMap(true)" in command


# ------------------------------------------------------------------
# Device neighbors
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_device_neighbors(client):
    with _mock_arcadedb_result({"result": [{"name": ["Kitchen"]}]}) as query:
        resp = client.get("/graph/devices/%232:0/neighbors")
    assert resp.status_code == 200
    assert len(resp.json()["neighbors"]) == 1
    command = query.await_args.args[1]
    assert ".bothE().otherV().valueMap(true)" in command


@pytest.mark.anyio
async def test_device_neighbors_escapes_rid_literals(client):
    with _mock_arcadedb_result({"result": []}) as query:
        resp = client.get("/graph/devices/%232:%27bad/neighbors")

    assert resp.status_code == 200
    command = query.await_args.args[1]
    assert "#2:\\'bad" in command


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
    assert "mutating" in resp.json()["detail"]


@pytest.mark.anyio
async def test_raw_query_requires_gremlin_traversal(client):
    resp = client.post(
        "/graph/query",
        json={"query": "SELECT FROM Room"},
    )
    assert resp.status_code == 400
    assert "g." in resp.json()["detail"]


@pytest.mark.anyio
async def test_raw_query_non_admin(client, guest_user):
    app.dependency_overrides[get_current_user] = lambda: guest_user
    resp = client.post(
        "/graph/query",
        json={"query": "g.V().valueMap()"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_graph_sync_admin(client):
    session = AsyncMock()
    app.dependency_overrides[get_mysql_session] = lambda: session
    sync_result = {
        "changed_rooms": 1,
        "changed_devices": 2,
        "changed_sensor_readings": 3,
        "last_sync": "2026-05-24 15:00:00",
        "conflict_policy": "skip",
    }

    with _mock_builder_sync(sync_result) as sync:
        resp = client.post(
            "/graph/sync",
            json={
                "last_sync": "2026-05-24 15:00:00",
                "conflict_policy": "skip",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == sync_result
    sync.assert_awaited_once_with(
        mysql_session=session,
        last_sync="2026-05-24 15:00:00",
        conflict_policy="skip",
    )


@pytest.mark.anyio
async def test_graph_sync_non_admin_forbidden(client, guest_user):
    app.dependency_overrides[get_current_user] = lambda: guest_user
    app.dependency_overrides[get_mysql_session] = lambda: AsyncMock()

    with _mock_builder_sync({}) as sync:
        resp = client.post("/graph/sync", json={})

    assert resp.status_code == 403
    sync.assert_not_awaited()


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
async def test_sync_rooms_to_graph_returns_rid_map_and_uses_upserts():
    session = AsyncMock()
    session.execute.return_value = _mock_mysql_rows(
        [
            {"id": 1, "name": "Kitchen"},
            {"id": 2, "name": "Garage"},
        ]
    )

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(
            side_effect=[
                {"result": []},
                _mock_arcadedb_rid("#1:0"),
                _mock_arcadedb_rid("#1:1"),
                {"result": []},
            ]
        ),
    ) as query:
        rid_map = await sync_rooms_to_graph(session)

    assert rid_map == {1: "#1:0", 2: "#1:1"}
    commands = [call.args[1] for call in query.await_args_list]
    assert commands[0] == "BEGIN"
    assert commands[-1] == "COMMIT"
    assert any("UPDATE Room SET" in command for command in commands)
    assert any("UPSERT WHERE mysql_id = 1" in command for command in commands)


@pytest.mark.anyio
async def test_sync_devices_to_graph_returns_rids_and_repairs_location_edges():
    session = AsyncMock()
    session.execute.return_value = _mock_mysql_rows(
        [
            _device_row(id=99, room_id=10, room_name="Kitchen"),
        ]
    )

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(
            side_effect=[
                {"result": []},
                _mock_arcadedb_rid("#2:0"),
                {"result": []},
                {"result": []},
                {"result": []},
            ]
        ),
    ) as query:
        rid_map = await sync_devices_to_graph(session)

    assert rid_map == {99: "#2:0"}
    commands = [call.args[1] for call in query.await_args_list]
    assert commands[0] == "BEGIN"
    assert commands[-1] == "COMMIT"
    assert any("UPDATE Device SET" in command for command in commands)
    assert any("UPSERT WHERE mysql_id = 99" in command for command in commands)
    assert any("DELETE EDGE LOCATED_IN" in command for command in commands)
    assert any("CREATE EDGE LOCATED_IN" in command for command in commands)


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
        and "ha_domain =" not in command
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
async def test_sync_sensor_readings_skips_arcadedb_when_no_rows_changed():
    session = AsyncMock()
    session.execute.return_value = _mock_mysql_rows([])

    with patch(
        "orchestrator.graph.builder.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        result = await sync_sensor_readings_to_graph(session)

    assert result["changed_sensor_readings"] == 0
    query.assert_not_awaited()


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


def test_default_seed_inventory_uses_real_room_and_device_inventory():
    inventory = default_seed_inventory()

    assert len(inventory.rooms) == 14
    assert len(inventory.devices) == 33
    assert any(
        room.name == "Kitchen" and room.mysql_id == 1 for room in inventory.rooms
    )
    assert any(
        device.name == "Balance" and device.device_type == "EnergyMonitor"
        for device in inventory.devices
    )
    assert any(sensor.name == "Motion Sensor Garage" for sensor in inventory.sensors)
    assert any(
        circuit.breaker_id == "kitchen_lights_breaker" for circuit in inventory.circuits
    )


def test_build_inventory_from_records_uses_home_assistant_device_area_mapping():
    inventory = build_inventory_from_records(
        rooms=[
            {"id": 1, "name": "Kitchen"},
        ],
        devices=[
            {
                "id": 10,
                "name": "Counter Light",
                "device_type": "light",
                "room_id": 1,
            }
        ],
        ha_entities=[
            {
                "entity_id": "light.counter_light",
                "device_id": "device-1",
                "area_id": None,
                "platform": "kasa",
            }
        ],
        ha_devices=[
            {
                "id": "device-1",
                "area_id": "kitchen",
                "manufacturer": "Kasa",
                "model": "KL125",
                "via_device_id": "hub-1",
            }
        ],
        ha_areas=[
            {
                "area_id": "kitchen",
                "floor_id": "1st_floor",
                "name": "Kitchen",
            }
        ],
    )

    room = inventory.rooms[0]
    device = inventory.devices[0]
    assert room.ha_area_id == "kitchen"
    assert room.floor_id == "1st_floor"
    assert device.ha_entity_id == "light.counter_light"
    assert device.ha_device_id == "device-1"
    assert device.ha_area_id == "kitchen"
    assert device.ha_domain == "light"
    assert device.device_type == "SmartBulb"
    assert device.manufacturer == "Kasa"
    assert device.model == "KL125"
    assert device.via_device_id == "hub-1"


def test_home_assistant_websocket_url_uses_matching_scheme():
    assert (
        _home_assistant_websocket_url("http://localhost:8123")
        == "ws://localhost:8123/api/websocket"
    )
    assert (
        _home_assistant_websocket_url("https://ha.example.com")
        == "wss://ha.example.com/api/websocket"
    )


def test_home_assistant_reasoning_relevance_prefers_energy_and_security_states():
    assert _is_reasoning_relevant_state(
        "sensor.panel_power",
        {"attributes": {"device_class": "power", "unit_of_measurement": "W"}},
    )
    assert _is_reasoning_relevant_state(
        "binary_sensor.front_door",
        {"attributes": {"device_class": "door"}},
    )
    assert not _is_reasoning_relevant_state(
        "update.router_firmware",
        {"attributes": {"device_class": "firmware"}},
    )


@pytest.mark.anyio
async def test_seed_graph_upserts_vertices_and_repairs_edges():
    inventory = GraphSeedInventory(
        rooms=[SeedRoom(mysql_id=1, name="Kitchen", room_type="Kitchen")],
        devices=[
            SeedDevice(
                mysql_id=10,
                name="Counter Light",
                device_type="SmartBulb",
                room_mysql_id=1,
                ha_domain="light",
                ha_entity_id="light.counter_light",
            )
        ],
    )

    with patch(
        "orchestrator.graph.seeds.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        result = await seed_graph(inventory)

    assert result.rooms == 1
    assert result.devices == 1
    assert result.edges == 2
    commands = [call.args[1] for call in query.await_args_list]
    assert any(
        "UPDATE Home SET" in command and "UPSERT WHERE name" in command
        for command in commands
    )
    assert any(
        "UPDATE Room SET" in command and "UPSERT WHERE mysql_id = 1" in command
        for command in commands
    )
    assert any(
        "UPDATE Device SET" in command and "UPSERT WHERE mysql_id = 10" in command
        for command in commands
    )
    assert any("DELETE EDGE CONTAINS" in command for command in commands)
    assert any("CREATE EDGE CONTAINS" in command for command in commands)
    assert any("DELETE EDGE LOCATED_IN" in command for command in commands)
    assert any("CREATE EDGE LOCATED_IN" in command for command in commands)


@pytest.mark.anyio
async def test_sync_graph_relationships_repairs_inferred_edges():
    session = AsyncMock()
    session.execute.return_value = _mock_mysql_rows(
        [
            {
                "email": "owner@example.com",
                "role": "homeowner",
                "household_id": 1,
                "is_active": True,
            }
        ]
    )

    async def fake_query(language, command, database=None, readonly=True):
        if language == "gremlin" and "hasLabel('Device').valueMap(true)" in command:
            return {
                "result": [
                    {
                        "@rid": "#1:0",
                        "name": ["Counter Light"],
                        "device_type": ["SmartBulb"],
                        "ha_entity_id": ["light.counter_light"],
                    }
                ]
            }
        if language == "gremlin" and "hasLabel('Room').valueMap(true)" in command:
            return {
                "result": [
                    {
                        "@rid": "#2:0",
                        "name": ["Kitchen"],
                        "ha_area_id": ["kitchen"],
                    }
                ]
            }
        if language == "gremlin" and ".in('LOCATED_IN')" in command:
            return {"result": [{"@rid": "#1:0", "name": ["Counter Light"]}]}
        if language == "gremlin" and "hasLabel('Sensor').valueMap(true)" in command:
            return {
                "result": [
                    {
                        "@rid": "#3:0",
                        "name": ["Counter Light"],
                        "ha_entity_id": ["light.counter_light"],
                    }
                ]
            }
        if language == "gremlin" and "hasLabel('User').values('@rid')" in command:
            return {"result": ["#4:0"]}
        if language == "gremlin" and "hasLabel('User').valueMap(true)" in command:
            return {
                "result": [
                    {
                        "@rid": "#4:0",
                        "email": ["owner@example.com"],
                        "role": ["homeowner"],
                    }
                ]
            }
        if (
            language == "sql"
            and command == "SELECT @rid FROM User WHERE role IN ['homeowner', 'superadmin']"
        ):
            return {"result": [{"@rid": "#4:0"}]}
        if language == "sql" and command.startswith("SELECT FROM"):
            return {"result": [{"@rid": "#9:0"}]}
        return {"result": []}

    with patch(
        "orchestrator.graph.relationships.arcadedb_query",
        new=AsyncMock(side_effect=fake_query),
    ) as query:
        result = await sync_graph_relationships(mysql_session=session)

    commands = [call.args[1] for call in query.await_args_list]

    assert result.users == 1
    assert result.capabilities == 9
    assert result.actions == 8
    assert result.requires_capability == 8
    assert result.has_capability == 3
    assert result.circuits == 1
    assert result.powered_by == 1
    assert result.derived_from == 1
    assert result.owns == 1
    assert result.can_perform == 8
    assert any("CREATE EDGE HAS_CAPABILITY" in command for command in commands)
    assert any("CREATE EDGE REQUIRES_CAPABILITY" in command for command in commands)
    assert any("CREATE EDGE POWERED_BY" in command for command in commands)
    assert any("CREATE EDGE DERIVED_FROM" in command for command in commands)
    assert any("CREATE EDGE OWNS" in command for command in commands)
    assert any("CREATE EDGE CAN_PERFORM" in command for command in commands)


@pytest.mark.anyio
async def test_get_affected_rooms():
    with _mock_queries_arcadedb(
        {
            "result": [
                {"name": ["Kitchen"]},
                {"name": ["Garage"]},
            ]
        }
    ) as query:
        result = await get_affected_rooms("#12:0")

    assert len(result) == 2
    command = query.await_args.args[1]
    assert "out('LOCATED_IN')" in command
    assert "in('DEPENDS_ON').out('LOCATED_IN')" in command
    assert "out('POWERED_BY').in('POWERED_BY').out('LOCATED_IN')" in command
    assert ".dedup().valueMap(true)" in command


@pytest.mark.anyio
async def test_get_room_sensor_confidence():
    with _mock_queries_arcadedb(
        {
            "result": [
                {
                    "sensor": {"name": ["Hallway Motion"]},
                    "confidence": 0.82,
                }
            ]
        }
    ) as query:
        result = await get_room_sensor_confidence("#10:0")

    assert result[0]["confidence"] == 0.82
    command = query.await_args.args[1]
    assert ".inE('MONITORS')" in command
    assert ".by(valueMap(true))" in command
    assert ".by(select('edge').values('confidence_score'))" in command
