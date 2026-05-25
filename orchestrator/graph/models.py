"""Pydantic models for ArcadeDB vertex and edge types."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from orchestrator.core.permissions import Role


def utc_now() -> datetime:
    """Return the current UTC timestamp with timezone information."""
    return datetime.now(UTC)


class GraphModel(BaseModel):
    """Shared model settings for graph payloads."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class RoomType(str, Enum):
    """Known smart-home room categories."""

    BEDROOM = "Bedroom"
    KITCHEN = "Kitchen"
    GARAGE = "Garage"
    LIVING_ROOM = "LivingRoom"
    BATHROOM = "Bathroom"
    MEDIA_ROOM = "MediaRoom"
    OFFICE = "Office"
    LAUNDRY = "Laundry"
    OUTDOOR = "Outdoor"
    UTILITY = "Utility"
    OTHER = "Other"


class HomeAssistantDomain(str, Enum):
    """Home Assistant domains observed in the local registry exports."""

    AUTOMATION = "automation"
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    CLIMATE = "climate"
    COVER = "cover"
    DEVICE_TRACKER = "device_tracker"
    EVENT = "event"
    FAN = "fan"
    INPUT_BOOLEAN = "input_boolean"
    LIGHT = "light"
    MEDIA_PLAYER = "media_player"
    NUMBER = "number"
    PERSON = "person"
    SELECT = "select"
    SENSOR = "sensor"
    SWITCH = "switch"
    TODO = "todo"
    TTS = "tts"
    UPDATE = "update"
    VALVE = "valve"
    WEATHER = "weather"


class DeviceType(str, Enum):
    """Known device categories stored in ArcadeDB."""

    ENERGY_MONITOR = "EnergyMonitor"
    SMART_PLUG = "SmartPlug"
    SMART_BULB = "SmartBulb"
    MOTION_SENSOR = "MotionSensor"
    SOUND_SENSOR = "SoundSensor"
    THERMOSTAT = "Thermostat"
    SMART_SWITCH = "SmartSwitch"
    COVER = "Cover"
    CLIMATE = "Climate"
    VALVE = "Valve"
    FAN = "Fan"
    MEDIA_PLAYER = "MediaPlayer"
    AUTOMATION = "Automation"
    BUTTON = "Button"
    DEVICE_TRACKER = "DeviceTracker"
    EVENT = "Event"
    INPUT_BOOLEAN = "InputBoolean"
    NUMBER = "Number"
    PERSON = "Person"
    SELECT = "Select"
    TODO = "Todo"
    TTS = "TTS"
    UPDATE = "Update"
    WEATHER = "Weather"
    SENSOR = "Sensor"
    OTHER = "Other"


class SensorType(str, Enum):
    """Known sensor reading categories."""

    MOTION = "motion"
    SOUND = "sound"
    POWER = "power"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    SOIL = "soil"
    OCCUPANCY = "occupancy"
    CONTACT = "contact"
    LIGHT = "light"
    ENERGY = "energy"
    VOLTAGE = "voltage"
    CURRENT = "current"
    FREQUENCY = "frequency"


class CapabilityName(str, Enum):
    """Known device capabilities from the EcoNest ontology."""

    ON_OFF = "OnOff"
    DIMMABLE = "Dimmable"
    COLOR_CONTROL = "ColorControl"
    POWER_MONITORING = "PowerMonitoring"
    MOTION_DETECTION = "MotionDetection"
    SOUND_DETECTION = "SoundDetection"
    TEMPERATURE_CONTROL = "TemperatureControl"
    COVER_CONTROL = "CoverControl"
    WATER_CONTROL = "WaterControl"


class ActionName(str, Enum):
    """Known graph action names."""

    TURN_ON = "TurnOn"
    TURN_OFF = "TurnOff"
    SET_BRIGHTNESS = "SetBrightness"
    SET_COLOR_TEMP = "SetColorTemp"
    SET_TEMPERATURE = "SetTemperature"
    OPEN = "Open"
    CLOSE = "Close"
    READ_STATE = "ReadState"


class PermissionName(str, Enum):
    """Permissions used by access-control edges."""

    DEVICE_READ = "device:read"
    DEVICE_WRITE = "device:write"
    DEVICE_ADMIN = "device:admin"
    ROOM_READ = "room:read"
    ROOM_WRITE = "room:write"
    ROOM_ADMIN = "room:admin"
    AGENT_RUN = "agent:run"
    AGENT_ADMIN = "agent:admin"
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    USER_ADMIN = "user:admin"


class VertexModel(GraphModel):
    """Base fields shared by all graph vertices."""

    created_at: datetime = Field(default_factory=utc_now)


HA_DOMAIN_DEVICE_TYPE_MAP: dict[HomeAssistantDomain, DeviceType] = {
    HomeAssistantDomain.AUTOMATION: DeviceType.AUTOMATION,
    HomeAssistantDomain.BINARY_SENSOR: DeviceType.SENSOR,
    HomeAssistantDomain.BUTTON: DeviceType.BUTTON,
    HomeAssistantDomain.CLIMATE: DeviceType.CLIMATE,
    HomeAssistantDomain.COVER: DeviceType.COVER,
    HomeAssistantDomain.DEVICE_TRACKER: DeviceType.DEVICE_TRACKER,
    HomeAssistantDomain.EVENT: DeviceType.EVENT,
    HomeAssistantDomain.FAN: DeviceType.FAN,
    HomeAssistantDomain.INPUT_BOOLEAN: DeviceType.INPUT_BOOLEAN,
    HomeAssistantDomain.LIGHT: DeviceType.SMART_BULB,
    HomeAssistantDomain.MEDIA_PLAYER: DeviceType.MEDIA_PLAYER,
    HomeAssistantDomain.NUMBER: DeviceType.NUMBER,
    HomeAssistantDomain.PERSON: DeviceType.PERSON,
    HomeAssistantDomain.SELECT: DeviceType.SELECT,
    HomeAssistantDomain.SENSOR: DeviceType.SENSOR,
    HomeAssistantDomain.SWITCH: DeviceType.SMART_SWITCH,
    HomeAssistantDomain.TODO: DeviceType.TODO,
    HomeAssistantDomain.TTS: DeviceType.TTS,
    HomeAssistantDomain.UPDATE: DeviceType.UPDATE,
    HomeAssistantDomain.VALVE: DeviceType.VALVE,
    HomeAssistantDomain.WEATHER: DeviceType.WEATHER,
}


def device_type_for_ha_domain(domain: HomeAssistantDomain | str) -> DeviceType:
    """Return the graph device type that best matches a Home Assistant domain."""
    try:
        normalized = HomeAssistantDomain(domain)
    except ValueError:
        return DeviceType.OTHER
    return HA_DOMAIN_DEVICE_TYPE_MAP[normalized]


class Home(VertexModel):
    """A household or physical smart-home installation."""

    name: str = Field(min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    home_assistant_url: str | None = Field(default=None, max_length=255)


class Room(VertexModel):
    """A physical or logical room in a home."""

    name: str = Field(min_length=1, max_length=100)
    room_type: RoomType
    description: str | None = Field(default=None, max_length=255)
    ha_area_id: str | None = Field(default=None, max_length=100)
    floor_id: str | None = Field(default=None, max_length=100)


class Device(VertexModel):
    """A controllable or observable smart-home device."""

    name: str = Field(min_length=1, max_length=100)
    device_type: DeviceType
    ha_domain: HomeAssistantDomain | None = None
    ha_entity_id: str | None = Field(default=None, max_length=255)
    ha_device_id: str | None = Field(default=None, max_length=64)
    ha_area_id: str | None = Field(default=None, max_length=100)
    ha_platform: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    ip_address: str | None = Field(default=None, max_length=50)
    via_device_id: str | None = Field(default=None, max_length=64)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_ha_domain_matches_entity_id(self) -> "Device":
        """Keep HA domain metadata consistent with entity IDs when both exist."""
        if self.ha_domain and self.ha_entity_id:
            entity_domain = self.ha_entity_id.split(".", maxsplit=1)[0]
            if entity_domain != self.ha_domain:
                raise ValueError("ha_domain must match the ha_entity_id domain")
        return self


class Circuit(VertexModel):
    """Electrical circuit or breaker grouping."""

    name: str = Field(min_length=1, max_length=100)
    breaker_id: str | None = Field(default=None, max_length=100)
    max_amperage: float | None = Field(default=None, gt=0)


class Sensor(VertexModel):
    """Sensor metadata for observed home readings."""

    name: str = Field(min_length=1, max_length=100)
    sensor_type: SensorType
    unit: str | None = Field(default=None, max_length=32)
    ha_entity_id: str | None = Field(default=None, max_length=255)
    device_class: str | None = Field(default=None, max_length=100)
    state_class: str | None = Field(default=None, max_length=100)


class SensorReading(GraphModel):
    """A point-in-time reading mirrored from MySQL sensor_readings."""

    mysql_id: int = Field(ge=1)
    device_id: int = Field(ge=1)
    room_id: int = Field(ge=1)
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class User(VertexModel):
    """A user vertex mirrored into the graph access model."""

    email: EmailStr
    role: Role
    household_id: int | None = Field(default=None, ge=1)
    is_active: bool = True


class Capability(GraphModel):
    """A capability that a device can expose or an action can require."""

    name: CapabilityName
    description: str | None = Field(default=None, max_length=255)


class Action(GraphModel):
    """A graph action that can be performed against capable devices."""

    name: ActionName
    parameters: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Reject empty parameter names that cannot map to service calls."""
        if any(not str(key).strip() for key in value):
            raise ValueError("Action parameter names must be non-empty")
        return value


class EdgeModel(GraphModel):
    """Base fields shared by all graph edges."""

    from_id: str = Field(alias="from", min_length=1)
    to_id: str = Field(alias="to", min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class Contains(EdgeModel):
    """Home -> Room, Room -> Device."""


class PoweredBy(EdgeModel):
    """Device -> Circuit."""


class Monitors(EdgeModel):
    """Sensor -> Room."""

    confidence_score: float | None = Field(default=None, ge=0, le=1)


class Owns(EdgeModel):
    """User -> Home."""


class HasAccess(EdgeModel):
    """User -> Room or User -> Device with optional time restrictions."""

    permission: PermissionName
    allowed_start_hour: int | None = Field(default=None, ge=0, le=23)
    allowed_end_hour: int | None = Field(default=None, ge=0, le=23)

    @model_validator(mode="after")
    def validate_time_window_pair(self) -> "HasAccess":
        """Require both ends of the time window when either is present."""
        if (self.allowed_start_hour is None) != (self.allowed_end_hour is None):
            raise ValueError(
                "allowed_start_hour and allowed_end_hour must be provided together"
            )
        return self


class CanPerform(EdgeModel):
    """User -> Action."""


class HasCapability(EdgeModel):
    """Device -> Capability."""


class RequiresCapability(EdgeModel):
    """Action -> Capability."""


class DependsOn(EdgeModel):
    """Device -> Device."""


class LocatedIn(EdgeModel):
    """Device -> Room."""


UserVertex = User
BaseEdge = EdgeModel
