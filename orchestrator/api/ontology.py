"""Ontology API routes."""

import os
import tempfile
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.permissions import USER_ADMIN, has_permission
from orchestrator.ontology.loader import load_ontology
from orchestrator.ontology.reasoner import run_reasoner
from orchestrator.ontology.validator import validate_graph

router = APIRouter(prefix="/ontology", tags=["ontology"])


@router.get("")
async def list_ontology(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """List ontology classes and properties."""
    # Return static summary from smart_home.ttl
    return {
        "classes": [
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
            "User",
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
            "Sensor",
            "SensorReading",
        ],
        "object_properties": [
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
        ],
        "data_properties": [
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
            "brightness",
            "colorTemperature",
            "ipAddress",
            "breakerId",
            "maxAmperage",
            "sensorType",
            "unit",
            "deviceClass",
            "stateClass",
            "timestamp",
            "confidenceScore",
            "confidence",
            "observationValue",
            "observationTimestamp",
        ],
    }


@router.get("/classes/{name}")
async def get_class(
    name: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Class details with restrictions."""
    class_details: dict[str, dict[str, Any]] = {
        "SmartBulb": {
            "superclass": "Device",
            "inferred_capabilities": ["Dimmable"],
        },
        "MotionSensor": {
            "superclass": "Device",
            "must_monitor": "exactly_one_room",
        },
        "Dimmable": {
            "superclass": "Capability",
            "requires_property": "brightness",
        },
        "SetBrightness": {
            "superclass": "Action",
            "requires_capability": "Dimmable",
        },
        "SetColorTemp": {
            "superclass": "Action",
            "requires_capability": "ColorControl",
        },
        "EnergyMonitor": {
            "superclass": "Device",
            "description": "Energy devices and breaker-like loads from EcoNest MySQL",
        },
        "Office": {
            "superclass": "Room",
            "maps_rooms": ["Study Room"],
        },
        "Utility": {
            "superclass": "Room",
            "maps_rooms": ["HVAC"],
        },
        "SetTemperature": {
            "superclass": "Action",
            "requires_capability": "TemperatureControl",
        },
        "Open": {
            "superclass": "Action",
            "requires_capability": "CoverControl",
        },
        "Close": {
            "superclass": "Action",
            "requires_capability": "CoverControl",
        },
    }
    if name not in class_details:
        raise HTTPException(status_code=404, detail="Class not found")
    return {"name": name, **class_details[name]}


@router.get("/validate")
async def validate(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run validation on current graph."""
    return await validate_graph()


@router.post("/reason")
async def reason(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run reasoner and return inferred triples."""
    return await run_reasoner()


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_ontology(
    file: UploadFile,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Upload new Turtle file (admin only)."""
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    if not file.filename or not file.filename.endswith(".ttl"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .ttl files are accepted",
        )
    content = await file.read()
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, "uploaded_ontology.ttl")
    # temp_path = "/tmp/uploaded_ontology.ttl"
    with open(temp_path, "wb") as f:
        f.write(content)
    result = await load_ontology(temp_path)
    return {"message": "Ontology uploaded", "summary": result}
