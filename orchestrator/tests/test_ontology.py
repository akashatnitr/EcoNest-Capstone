"""Tests for the ontology layer."""

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
    assert any("SmartBulb" in command and "Device" in command for command in commands)
    assert not any(
        "] TO" in command or "WHERE name = 'n" in command
        for command in subclass_commands
    )


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
