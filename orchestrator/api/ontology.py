"""Ontology API routes."""

from typing import Annotated

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
):
    """List ontology classes and properties."""
    # Return static summary from smart_home.ttl
    return {
        "classes": [
            "Room",
            "Bedroom",
            "Kitchen",
            "Garage",
            "LivingRoom",
            "MediaRoom",
            "Device",
            "SmartPlug",
            "SmartBulb",
            "MotionSensor",
            "SoundSensor",
            "Thermostat",
            "SmartSwitch",
            "Capability",
            "OnOff",
            "Dimmable",
            "ColorControl",
            "PowerMonitoring",
            "MotionDetection",
            "SoundDetection",
            "User",
            "Action",
        ],
        "object_properties": [
            "hasCapability",
            "requiresCapability",
            "locatedIn",
            "monitors",
            "canPerform",
            "owns",
        ],
        "data_properties": [
            "brightness",
            "colorTemperature",
            "ipAddress",
        ],
    }


@router.get("/classes/{name}")
async def get_class(
    name: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Class details with restrictions."""
    class_details = {
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
    }
    if name not in class_details:
        raise HTTPException(status_code=404, detail="Class not found")
    return {"name": name, **class_details[name]}


@router.get("/validate")
async def validate(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Run validation on current graph."""
    return await validate_graph()


@router.post("/reason")
async def reason(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Run reasoner and return inferred triples."""
    return await run_reasoner()


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_ontology(
    file: UploadFile,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
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
    temp_path = "/tmp/uploaded_ontology.ttl"
    with open(temp_path, "wb") as f:
        f.write(content)
    result = await load_ontology(temp_path)
    return {"message": "Ontology uploaded", "summary": result}
