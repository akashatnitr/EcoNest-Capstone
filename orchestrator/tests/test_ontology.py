"""Tests for the ontology layer."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from rdflib import OWL, RDF, RDFS, Graph, Namespace

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.graph.models import ActionName, CapabilityName, DeviceType, RoomType
from orchestrator.graph.seeds import default_seed_inventory
from orchestrator.main import app
from orchestrator.ontology.loader import load_ontology
from orchestrator.ontology.reasoner import load_rules, run_reasoner

ECONEST = Namespace("http://econest.example.org/ontology#")
SMART_HOME_TTL = Path(__file__).parents[1] / "ontology" / "smart_home.ttl"


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


@pytest.fixture(autouse=True)
def override_deps(admin_user):
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_mysql_session] = AsyncMock
    yield
    app.dependency_overrides.clear()


def load_smart_home_graph() -> Graph:
    graph = Graph()
    graph.parse(SMART_HOME_TTL, format="turtle")
    return graph


def ontology_class_name(value: str) -> str:
    if value == RoomType.OTHER.value:
        return "OtherRoom"
    if value == DeviceType.OTHER.value:
        return "OtherDevice"
    if value == DeviceType.PERSON.value:
        return "PersonDevice"
    return value


def write_rules(tmp_path: Path, rules: dict) -> str:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------
# List ontology
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_ontology(client):
    resp = client.get("/ontology")
    assert resp.status_code == 200
    data = resp.json()
    assert "Room" in data["classes"]
    assert "TurnOn" in data["classes"]
    assert "EnergyMonitor" in data["classes"]
    assert "Office" in data["classes"]
    assert "Utility" in data["classes"]
    assert "hasCapability" in data["object_properties"]
    assert "contains" in data["object_properties"]
    assert "poweredBy" in data["object_properties"]
    assert "dependsOn" in data["object_properties"]
    assert "hasWattage" in data["data_properties"]
    assert "haEntityId" in data["data_properties"]


@pytest.mark.anyio
async def test_list_ontology_covers_seed_inventory_types(client):
    resp = client.get("/ontology")
    assert resp.status_code == 200
    data = resp.json()
    inventory = default_seed_inventory()

    for room in inventory.rooms:
        assert ontology_class_name(room.room_type) in data["classes"]
    for device in inventory.devices:
        assert ontology_class_name(device.device_type) in data["classes"]


def test_smart_home_ttl_parses():
    graph = load_smart_home_graph()
    assert len(graph) > 0


def test_smart_home_ttl_defines_issue_19_classes():
    graph = load_smart_home_graph()
    expected_classes = [
        "Room",
        "Bedroom",
        "Kitchen",
        "Garage",
        "LivingRoom",
        "Bathroom",
        "MediaRoom",
        "Office",
        "Laundry",
        "Outdoor",
        "Utility",
        "OtherRoom",
        "Device",
        "EnergyMonitor",
        "SmartPlug",
        "SmartBulb",
        "MotionSensor",
        "SoundSensor",
        "Thermostat",
        "SmartSwitch",
        "Cover",
        "Climate",
        "Valve",
        "Fan",
        "MediaPlayer",
        "Automation",
        "Button",
        "DeviceTracker",
        "Event",
        "InputBoolean",
        "Number",
        "PersonDevice",
        "Select",
        "Todo",
        "TTS",
        "Update",
        "Weather",
        "Sensor",
        "OtherDevice",
        "Capability",
        "OnOff",
        "Dimmable",
        "ColorControl",
        "PowerMonitoring",
        "MotionDetection",
        "SoundDetection",
        "TemperatureControl",
        "CoverControl",
        "WaterControl",
        "Action",
        "TurnOn",
        "TurnOff",
        "SetBrightness",
        "SetColorTemp",
        "SetTemperature",
        "Open",
        "Close",
        "ReadState",
        "Circuit",
        "SensorReading",
    ]

    for class_name in expected_classes:
        assert (ECONEST[class_name], RDF.type, OWL.Class) in graph


def test_smart_home_ttl_covers_graph_model_enums():
    graph = load_smart_home_graph()
    enum_values = [
        *(room_type.value for room_type in RoomType),
        *(device_type.value for device_type in DeviceType),
        *(capability.value for capability in CapabilityName),
        *(action.value for action in ActionName),
    ]

    for value in enum_values:
        assert (ECONEST[ontology_class_name(value)], RDF.type, OWL.Class) in graph


def test_smart_home_ttl_defines_issue_19_properties():
    graph = load_smart_home_graph()

    for property_name in [
        "hasCapability",
        "requiresCapability",
        "locatedIn",
        "contains",
        "monitors",
        "poweredBy",
        "dependsOn",
        "canPerform",
        "owns",
        "hasAccess",
        "observedIn",
        "derivedFrom",
    ]:
        assert (ECONEST[property_name], RDF.type, OWL.ObjectProperty) in graph

    for property_name in [
        "hasName",
        "mysqlId",
        "roomType",
        "haAreaId",
        "floorId",
        "hasManufacturer",
        "hasModel",
        "hasPowerRating",
        "hasWattage",
        "haDomain",
        "haEntityId",
        "haDeviceId",
        "haPlatform",
        "viaDeviceId",
        "isActive",
        "breakerId",
        "maxAmperage",
        "sensorType",
        "unit",
        "deviceClass",
        "stateClass",
        "timestamp",
        "confidenceScore",
    ]:
        assert (ECONEST[property_name], RDF.type, OWL.DatatypeProperty) in graph


def test_smart_bulb_requires_dimmable_capability():
    graph = load_smart_home_graph()
    restrictions = list(graph.objects(ECONEST.SmartBulb, RDFS.subClassOf))

    assert any(
        (restriction, OWL.onProperty, ECONEST.hasCapability) in graph
        and (restriction, OWL.someValuesFrom, ECONEST.Dimmable) in graph
        for restriction in restrictions
    )


def test_motion_sensor_monitors_exactly_one_room():
    graph = load_smart_home_graph()
    restrictions = list(graph.objects(ECONEST.MotionSensor, RDFS.subClassOf))

    assert any(
        (restriction, OWL.onProperty, ECONEST.monitors) in graph
        and (restriction, OWL.onClass, ECONEST.Room) in graph
        and graph.value(restriction, OWL.qualifiedCardinality).toPython() == 1
        for restriction in restrictions
    )


@pytest.mark.anyio
async def test_load_ontology_ignores_restriction_blank_nodes():
    with patch(
        "orchestrator.ontology.loader.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as mock_query:
        result = await load_ontology(str(SMART_HOME_TTL))

    commands = [call.args[1] for call in mock_query.await_args_list]
    subclass_commands = [
        command for command in commands if "CREATE EDGE SUBCLASS_OF" in command
    ]

    assert "SmartBulb" in result["classes"]
    assert "Room" in result["vertex_types"]
    assert "Device" in result["vertex_types"]
    assert any("SmartBulb" in command and "Device" in command for command in commands)
    assert not any(
        "] TO" in command or "WHERE name = 'n" in command
        for command in subclass_commands
    )


@pytest.mark.anyio
async def test_load_ontology_uses_arcadedb_graph_names():
    with patch(
        "orchestrator.ontology.loader.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as mock_query:
        result = await load_ontology(str(SMART_HOME_TTL))

    commands = [call.args[1] for call in mock_query.await_args_list]

    assert "HAS_CAPABILITY" in result["edges"]
    assert "POWERED_BY" in result["edges"]
    assert "hasCapability" not in result["edges"]
    assert any("CREATE EDGE TYPE HAS_CAPABILITY IF NOT EXISTS" in c for c in commands)
    assert not any("CREATE EDGE TYPE hasCapability" in c for c in commands)


@pytest.mark.anyio
async def test_load_ontology_maps_data_properties_to_existing_schema_names():
    with patch(
        "orchestrator.ontology.loader.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as mock_query:
        result = await load_ontology(str(SMART_HOME_TTL))

    commands = [call.args[1] for call in mock_query.await_args_list]

    assert "Device.ha_entity_id" in result["properties"]
    assert "Device.power_rating" in result["properties"]
    assert "Circuit.breaker_id" in result["properties"]
    assert "MONITORS.confidence_score" in result["properties"]
    assert any(
        "CREATE PROPERTY Device.ha_entity_id IF NOT EXISTS STRING" in c
        for c in commands
    )
    assert any(
        "CREATE PROPERTY Circuit.breaker_id IF NOT EXISTS STRING" in c for c in commands
    )
    assert any(
        "CREATE PROPERTY MONITORS.confidence_score IF NOT EXISTS FLOAT" in c
        for c in commands
    )


@pytest.mark.anyio
async def test_load_ontology_upserts_metadata_idempotently():
    with patch(
        "orchestrator.ontology.loader.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as mock_query:
        await load_ontology(str(SMART_HOME_TTL))

    commands = [call.args[1] for call in mock_query.await_args_list]

    assert any(command.startswith("UPDATE Class SET") for command in commands)
    assert any(command.startswith("UPDATE Property SET") for command in commands)
    assert any("UPSERT WHERE name = 'SmartBulb'" in command for command in commands)
    assert any("DELETE EDGE SUBCLASS_OF" in command for command in commands)
    assert any("CREATE EDGE SUBCLASS_OF" in command for command in commands)


# ------------------------------------------------------------------
# Class details
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_class_smartbulb(client):
    resp = client.get("/ontology/classes/SmartBulb")
    assert resp.status_code == 200
    assert resp.json()["inferred_capabilities"] == ["Dimmable"]


@pytest.mark.anyio
async def test_get_class_not_found(client):
    resp = client.get("/ontology/classes/NonExistent")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Validate
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_validate(client):
    with patch(
        "orchestrator.api.ontology.validate_graph",
        new=AsyncMock(return_value={"valid": True, "errors": [], "error_count": 0}),
    ):
        resp = client.get("/ontology/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


# ------------------------------------------------------------------
# Reason
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_reason(client):
    with patch(
        "orchestrator.api.ontology.run_reasoner",
        new=AsyncMock(return_value={"inferred": [], "total": 0}),
    ):
        resp = client.post("/ontology/reason")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_load_reasoner_rules_from_json(tmp_path):
    rules_path = write_rules(
        tmp_path,
        {
            "capability_rules": [
                {"device_type": "EnergyMonitor", "capabilities": ["PowerMonitoring"]}
            ],
            "monitor_rules": [],
            "access_rules": [],
        },
    )

    rules = load_rules(rules_path)

    assert rules.capability_rules[0].device_type == "EnergyMonitor"
    assert rules.capability_rules[0].capabilities == ["PowerMonitoring"]


@pytest.mark.anyio
async def test_reasoner_infers_device_capability_edges(tmp_path):
    rules_path = write_rules(
        tmp_path,
        {
            "capability_rules": [
                {"device_type": "EnergyMonitor", "capabilities": ["PowerMonitoring"]}
            ],
            "monitor_rules": [],
            "access_rules": [],
        },
    )

    async def fake_query(language, command, database=None, readonly=True):
        if language == "gremlin":
            return {"result": ["#12:0"]}
        return {"result": []}

    with patch(
        "orchestrator.ontology.reasoner.arcadedb_query",
        new=AsyncMock(side_effect=fake_query),
    ) as query:
        result = await run_reasoner(rules_path)

    commands = [call.args[1] for call in query.await_args_list]

    assert result["total"] == 1
    assert result["inferred"][0]["capability"] == "PowerMonitoring"
    assert any("UPDATE Capability SET" in command for command in commands)
    assert any(
        "CREATE EDGE HAS_CAPABILITY FROM #12:0" in command for command in commands
    )
    assert any("WHERE name = 'PowerMonitoring'" in command for command in commands)


@pytest.mark.anyio
async def test_reasoner_infers_sensor_monitor_edges(tmp_path):
    rules_path = write_rules(
        tmp_path,
        {
            "capability_rules": [],
            "monitor_rules": [
                {
                    "device_type": "MotionSensor",
                    "sensor_type": "motion",
                    "confidence_score": 0.9,
                }
            ],
            "access_rules": [],
        },
    )

    async def fake_query(language, command, database=None, readonly=True):
        if language == "gremlin":
            return {
                "result": [
                    {
                        "device": {
                            "@rid": "#9:0",
                            "name": ["Motion Sensor"],
                            "ha_entity_id": ["binary_sensor.motion_sensor"],
                        },
                        "room": {"@rid": "#2:0", "name": ["Front Door"]},
                    }
                ]
            }
        return {"result": []}

    with patch(
        "orchestrator.ontology.reasoner.arcadedb_query",
        new=AsyncMock(side_effect=fake_query),
    ) as query:
        result = await run_reasoner(rules_path)

    commands = [call.args[1] for call in query.await_args_list]

    assert result["total"] == 1
    assert result["inferred"][0]["sensor"] == "Motion Sensor"
    assert any("UPDATE Sensor SET" in command for command in commands)
    assert any("DELETE EDGE MONITORS" in command for command in commands)
    assert any("CREATE EDGE MONITORS" in command for command in commands)
    assert any("confidence_score = 0.9" in command for command in commands)


@pytest.mark.anyio
async def test_reasoner_infers_user_can_perform_action_edges(tmp_path):
    rules_path = write_rules(
        tmp_path,
        {
            "capability_rules": [],
            "monitor_rules": [],
            "access_rules": [
                {
                    "role": "family_member",
                    "room_type": "Bedroom",
                    "action": "TurnOn",
                    "capability": "OnOff",
                }
            ],
        },
    )

    async def fake_query(language, command, database=None, readonly=True):
        if language == "gremlin":
            return {"result": [{"user": "#30:0", "device": "#12:0"}]}
        return {"result": []}

    with patch(
        "orchestrator.ontology.reasoner.arcadedb_query",
        new=AsyncMock(side_effect=fake_query),
    ) as query:
        result = await run_reasoner(rules_path)

    commands = [call.args[1] for call in query.await_args_list]

    assert result["total"] == 1
    assert result["inferred"][0]["action"] == "TurnOn"
    assert result["inferred"][0]["device_context"] == "#12:0"
    assert any("UPDATE Action SET" in command for command in commands)
    assert any("CREATE EDGE REQUIRES_CAPABILITY" in command for command in commands)
    assert any(
        "CREATE EDGE CAN_PERFORM FROM #30:0 TO (SELECT FROM Action WHERE name = 'TurnOn')"
        in command
        for command in commands
    )


# ------------------------------------------------------------------
# Upload
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_ttl_admin(client):
    with patch(
        "orchestrator.api.ontology.load_ontology",
        new=AsyncMock(return_value={"classes": ["TestClass"]}),
    ):
        resp = client.post(
            "/ontology/upload",
            files={"file": ("test.ttl", b"@prefix : <http://test#> .", "text/turtle")},
        )
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_upload_non_ttl_rejected(client):
    resp = client.post(
        "/ontology/upload",
        files={"file": ("test.xml", b"<xml/>", "text/xml")},
    )
    assert resp.status_code == 400
