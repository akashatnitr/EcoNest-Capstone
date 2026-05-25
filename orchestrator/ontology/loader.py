"""Parse RDF/Turtle and sync the EcoNest ontology into ArcadeDB."""

from pathlib import Path
from typing import Any, Optional

from rdflib import OWL, RDF, RDFS, XSD, Graph, URIRef

from orchestrator.core.database import arcadedb_query

DEFAULT_ONTOLOGY_PATH = Path(__file__).parent / "smart_home.ttl"

CORE_VERTEX_TYPES = {
    "Home",
    "Room",
    "Device",
    "Circuit",
    "Sensor",
    "SensorReading",
    "User",
    "Capability",
    "Action",
    "Observation",
}

ROOM_CLASSES = {
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
}

DEVICE_CLASSES = {
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
    "OtherDevice",
}

CAPABILITY_CLASSES = {
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
}

ACTION_CLASSES = {
    "Action",
    "TurnOn",
    "TurnOff",
    "SetBrightness",
    "SetColorTemp",
    "SetTemperature",
    "Open",
    "Close",
    "ReadState",
}

OBSERVATION_CLASSES = {
    "Observation",
    "OccupancyObservation",
    "EnergyObservation",
    "AnomalyObservation",
}

OBJECT_PROPERTY_EDGE_TYPES = {
    "hasCapability": "HAS_CAPABILITY",
    "requiresCapability": "REQUIRES_CAPABILITY",
    "locatedIn": "LOCATED_IN",
    "contains": "CONTAINS",
    "monitors": "MONITORS",
    "poweredBy": "POWERED_BY",
    "dependsOn": "DEPENDS_ON",
    "canPerform": "CAN_PERFORM",
    "owns": "OWNS",
    "hasAccess": "HAS_ACCESS",
    "observedIn": "OBSERVED_IN",
    "derivedFrom": "DERIVED_FROM",
}

DATA_PROPERTY_FIELD_NAMES = {
    "hasName": "name",
    "mysqlId": "mysql_id",
    "roomType": "room_type",
    "haAreaId": "ha_area_id",
    "floorId": "floor_id",
    "hasManufacturer": "manufacturer",
    "hasModel": "model",
    "hasPowerRating": "power_rating",
    "hasWattage": "wattage",
    "haDomain": "ha_domain",
    "haEntityId": "ha_entity_id",
    "haDeviceId": "ha_device_id",
    "haPlatform": "ha_platform",
    "viaDeviceId": "via_device_id",
    "isActive": "is_active",
    "colorTemperature": "color_temperature",
    "ipAddress": "ip_address",
    "breakerId": "breaker_id",
    "maxAmperage": "max_amperage",
    "sensorType": "sensor_type",
    "deviceClass": "device_class",
    "stateClass": "state_class",
    "confidenceScore": "confidence_score",
    "observationValue": "value",
    "observationTimestamp": "timestamp",
}

DATA_PROPERTY_TARGET_OVERRIDES = {
    "brightness": [("Device", "brightness", "INTEGER")],
    "colorTemperature": [("Device", "color_temperature", "INTEGER")],
    "confidenceScore": [("MONITORS", "confidence_score", "FLOAT")],
}

XSD_TO_ARCADE_TYPE = {
    XSD.boolean: "BOOLEAN",
    XSD.dateTime: "DATETIME",
    XSD.decimal: "FLOAT",
    XSD.float: "FLOAT",
    XSD.integer: "INTEGER",
    XSD.nonNegativeInteger: "INTEGER",
    XSD.string: "STRING",
}


async def load_ontology(
    turtle_path: Optional[str] = None,
) -> dict[str, Any]:
    """Parse a Turtle file and idempotently sync ontology metadata/schema.

    The RDF ontology uses readable lower-camel property names, while the graph
    schema uses concrete ArcadeDB labels such as HAS_CAPABILITY and LOCATED_IN.
    This loader keeps those two naming layers explicit so ontology loading does
    not drift from the graph code and seed data.
    """
    path = str(turtle_path or DEFAULT_ONTOLOGY_PATH)
    graph = Graph()
    graph.parse(path, format="turtle")

    classes = _ontology_classes(graph)
    object_properties = _ontology_properties(graph, OWL.ObjectProperty)
    data_properties = _ontology_properties(graph, OWL.DatatypeProperty)

    await _ensure_metadata_schema()

    vertex_types = sorted(
        {
            vertex_type
            for class_name in classes
            if (vertex_type := _vertex_type(class_name))
        }
    )
    for vertex_type in vertex_types:
        await arcadedb_query(
            "sql",
            f"CREATE VERTEX TYPE {vertex_type} IF NOT EXISTS",
            readonly=False,
        )

    for class_name, uri in classes.items():
        vertex_type = _vertex_type(class_name)
        await _upsert_metadata_vertex(
            "Class",
            {
                "name": class_name,
                "uri": str(uri),
                "vertex_type": vertex_type,
            },
        )

    subclass_edges = _subclass_edges(graph, classes)
    for child, parent in subclass_edges:
        child_selector = _metadata_selector("Class", child)
        parent_selector = _metadata_selector("Class", parent)
        await arcadedb_query(
            "sql",
            f"DELETE EDGE SUBCLASS_OF FROM {child_selector} TO {parent_selector}",
            readonly=False,
        )
        await arcadedb_query(
            "sql",
            f"CREATE EDGE SUBCLASS_OF FROM {child_selector} TO {parent_selector}",
            readonly=False,
        )

    edge_types: list[str] = []
    for property_name, uri in object_properties.items():
        edge_type = _edge_type(property_name)
        await arcadedb_query(
            "sql",
            f"CREATE EDGE TYPE {edge_type} IF NOT EXISTS",
            readonly=False,
        )
        await _upsert_metadata_vertex(
            "Property",
            {
                "name": property_name,
                "uri": str(uri),
                "property_type": "object",
                "graph_name": edge_type,
                "target_type": "edge",
            },
        )
        edge_types.append(edge_type)

    graph_properties: list[str] = []
    for property_name, uri in data_properties.items():
        targets = _data_property_targets(graph, uri, property_name)
        for owner_type, field_name, arcade_type in targets:
            await arcadedb_query(
                "sql",
                f"CREATE PROPERTY {owner_type}.{field_name} IF NOT EXISTS {arcade_type}",
                readonly=False,
            )
            graph_properties.append(f"{owner_type}.{field_name}")

        await _upsert_metadata_vertex(
            "Property",
            {
                "name": property_name,
                "uri": str(uri),
                "property_type": "data",
                "graph_name": ", ".join(
                    f"{owner}.{field}" for owner, field, _ in targets
                )
                or None,
                "target_type": "property",
            },
        )

    return {
        "classes": sorted(classes),
        "vertex_types": vertex_types,
        "edges": sorted(set(edge_types)),
        "properties": sorted(set(graph_properties)),
        "subclass_edges": len(subclass_edges),
        "file": path,
    }


async def _ensure_metadata_schema() -> None:
    metadata_commands = [
        "CREATE VERTEX TYPE Class IF NOT EXISTS",
        "CREATE PROPERTY Class.name IF NOT EXISTS STRING",
        "CREATE PROPERTY Class.uri IF NOT EXISTS STRING",
        "CREATE PROPERTY Class.vertex_type IF NOT EXISTS STRING",
        "CREATE VERTEX TYPE Property IF NOT EXISTS",
        "CREATE PROPERTY Property.name IF NOT EXISTS STRING",
        "CREATE PROPERTY Property.uri IF NOT EXISTS STRING",
        "CREATE PROPERTY Property.property_type IF NOT EXISTS STRING",
        "CREATE PROPERTY Property.graph_name IF NOT EXISTS STRING",
        "CREATE PROPERTY Property.target_type IF NOT EXISTS STRING",
        "CREATE EDGE TYPE SUBCLASS_OF IF NOT EXISTS",
    ]
    for command in metadata_commands:
        await arcadedb_query("sql", command, readonly=False)


async def _upsert_metadata_vertex(label: str, fields: dict[str, Any]) -> None:
    assignments = ", ".join(
        f"{name} = {_sql_value(value)}" for name, value in fields.items()
    )
    await arcadedb_query(
        "sql",
        f"UPDATE {label} SET {assignments} UPSERT WHERE name = {_sql_value(fields['name'])}",
        readonly=False,
    )


def _ontology_classes(graph: Graph) -> dict[str, URIRef]:
    return {
        _local_name(subject): subject
        for subject in graph.subjects(RDF.type, OWL.Class)
        if isinstance(subject, URIRef)
    }


def _ontology_properties(graph: Graph, property_type: URIRef) -> dict[str, URIRef]:
    return {
        _local_name(subject): subject
        for subject in graph.subjects(RDF.type, property_type)
        if isinstance(subject, URIRef)
    }


def _subclass_edges(
    graph: Graph,
    classes: dict[str, URIRef],
) -> list[tuple[str, str]]:
    known_uris = {uri: name for name, uri in classes.items()}
    edges: list[tuple[str, str]] = []
    for child_name, child_uri in classes.items():
        for parent_uri in graph.objects(child_uri, RDFS.subClassOf):
            if isinstance(parent_uri, URIRef) and parent_uri in known_uris:
                edges.append((child_name, known_uris[parent_uri]))
    return edges


def _data_property_targets(
    graph: Graph,
    property_uri: URIRef,
    property_name: str,
) -> list[tuple[str, str, str]]:
    if property_name in DATA_PROPERTY_TARGET_OVERRIDES:
        return DATA_PROPERTY_TARGET_OVERRIDES[property_name]

    field_name = DATA_PROPERTY_FIELD_NAMES.get(
        property_name, _snake_case(property_name)
    )
    arcade_type = _arcade_type(next(graph.objects(property_uri, RDFS.range), None))
    targets: list[tuple[str, str, str]] = []
    for domain_uri in graph.objects(property_uri, RDFS.domain):
        if not isinstance(domain_uri, URIRef):
            continue
        target = _vertex_type(_local_name(domain_uri))
        if target is not None:
            targets.append((target, field_name, arcade_type))
    return sorted(set(targets))


def _vertex_type(class_name: str) -> str | None:
    if class_name in CORE_VERTEX_TYPES:
        return class_name
    if class_name in ROOM_CLASSES:
        return "Room"
    if class_name in DEVICE_CLASSES:
        return "Device"
    if class_name in CAPABILITY_CLASSES:
        return "Capability"
    if class_name in ACTION_CLASSES:
        return "Action"
    if class_name in OBSERVATION_CLASSES:
        return "Observation"
    return None


def _edge_type(property_name: str) -> str:
    return OBJECT_PROPERTY_EDGE_TYPES.get(
        property_name, _screaming_snake(property_name)
    )


def _arcade_type(range_uri: Any) -> str:
    if isinstance(range_uri, URIRef):
        return XSD_TO_ARCADE_TYPE.get(range_uri, "STRING")
    return "STRING"


def _metadata_selector(label: str, name: str) -> str:
    return f"(SELECT FROM {label} WHERE name = {_sql_value(name)})"


def _sql_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _local_name(uri: URIRef) -> str:
    value = str(uri)
    if "#" in value:
        return value.rsplit("#", maxsplit=1)[-1]
    return value.rstrip("/").rsplit("/", maxsplit=1)[-1]


def _snake_case(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _screaming_snake(value: str) -> str:
    return _snake_case(value).upper()
