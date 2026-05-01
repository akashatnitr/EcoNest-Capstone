"""Pydantic models for ArcadeDB vertex and edge types."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# Vertex models
# ------------------------------------------------------------------


class Home(BaseModel):
    name: str
    address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Room(BaseModel):
    name: str
    room_type: Literal[
        "Bedroom", "Kitchen", "Garage", "LivingRoom", "Bathroom", "MediaRoom"
    ]
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Device(BaseModel):
    name: str
    device_type: Literal[
        "SmartPlug",
        "SmartBulb",
        "MotionSensor",
        "SoundSensor",
        "Thermostat",
        "SmartSwitch",
    ]
    ha_entity_id: Optional[str] = None
    ip_address: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Circuit(BaseModel):
    name: str
    breaker_id: Optional[str] = None
    max_amperage: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Sensor(BaseModel):
    name: str
    sensor_type: Literal["motion", "sound", "power", "temperature", "humidity", "soil"]
    unit: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserVertex(BaseModel):
    email: str
    role: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Capability(BaseModel):
    name: str
    description: Optional[str] = None


class Action(BaseModel):
    name: str
    parameters: Optional[dict] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------------
# Edge models
# ------------------------------------------------------------------


class BaseEdge(BaseModel):
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Contains(BaseEdge):
    """Home -> Room, Room -> Device."""

    pass


class PoweredBy(BaseEdge):
    """Device -> Circuit."""

    pass


class Monitors(BaseEdge):
    """Sensor -> Room."""

    pass


class Owns(BaseEdge):
    """User -> Home."""

    pass


class HasAccess(BaseEdge):
    """User -> Room or User -> Device."""

    permission: str


class CanPerform(BaseEdge):
    """User -> Action."""

    pass


class HasCapability(BaseEdge):
    """Device -> Capability."""

    pass


class RequiresCapability(BaseEdge):
    """Action -> Capability."""

    pass


class DependsOn(BaseEdge):
    """Device -> Device."""

    pass


class LocatedIn(BaseEdge):
    """Device -> Room."""

    pass
