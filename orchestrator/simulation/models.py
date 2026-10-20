"""Versioned data contract for synthetic EcoNest households.

These models deliberately describe a household without creating Home Assistant
entities or writing to EcoNest's databases.  A later simulation runtime can
turn the same template into Home Assistant-compatible state and service calls.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SimulationModel(BaseModel):
    """Strict shared base class for simulator input models."""

    model_config = ConfigDict(extra="forbid")


class SimulatedRoom(SimulationModel):
    """One room or distinct area in a synthetic household."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=80)
    kind: str = Field(min_length=2, max_length=40)


class SimulatedDevice(SimulationModel):
    """A Home Assistant-shaped simulated entity and its starting state."""

    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    name: str = Field(min_length=2, max_length=120)
    room_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    domain: Literal[
        "binary_sensor",
        "climate",
        "cover",
        "light",
        "sensor",
        "switch",
    ]
    state: str = Field(min_length=1, max_length=80)
    attributes: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def entity_domain_matches_domain(self) -> "SimulatedDevice":
        """Keep the explicit domain and Home Assistant entity identifier aligned."""
        entity_domain = self.entity_id.partition(".")[0]
        if entity_domain != self.domain:
            raise ValueError("entity_id domain must match domain")
        return self


class SimulatedContext(SimulationModel):
    """Starting external and household facts available to the simulated home."""

    weather_summary: str = Field(min_length=2, max_length=160)
    outdoor_temperature_f: float
    household_mode: Literal["home", "away", "sleep", "party"] = "home"
    upcoming_events: list[str] = Field(default_factory=list)


class SimulatedScenarioEvent(SimulationModel):
    """A deterministic state change scheduled relative to scenario start."""

    at_minutes: int = Field(ge=0, le=43_200)
    event_type: Literal["context", "occupancy", "state_change"]
    description: str = Field(min_length=4, max_length=240)
    entity_id: str | None = Field(default=None, pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    state: str | None = Field(default=None, min_length=1, max_length=80)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def state_changes_target_an_entity(self) -> "SimulatedScenarioEvent":
        """Require device state transitions to identify their target."""
        if self.event_type == "state_change" and self.entity_id is None:
            raise ValueError("state_change events require entity_id")
        return self


class SimulatedScenario(SimulationModel):
    """Repeatable household situation used for agent and safety evaluation."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=500)
    events: list[SimulatedScenarioEvent] = Field(min_length=1)
    expected_outcomes: list[str] = Field(default_factory=list)


class SimulatedHouseholdTemplate(SimulationModel):
    """An isolated home definition from which runtime simulation state is created."""

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=3, max_length=120)
    timezone: str = Field(min_length=3, max_length=80)
    resident_count: int = Field(ge=1, le=12)
    rooms: list[SimulatedRoom] = Field(min_length=1)
    devices: list[SimulatedDevice] = Field(min_length=1)
    context: SimulatedContext
    scenarios: list[SimulatedScenario] = Field(default_factory=list)

    @field_validator("rooms")
    @classmethod
    def room_ids_are_unique(cls, rooms: list[SimulatedRoom]) -> list[SimulatedRoom]:
        """Reject ambiguous room targets before a simulator consumes a template."""
        if len({room.id for room in rooms}) != len(rooms):
            raise ValueError("room ids must be unique")
        return rooms

    @field_validator("devices")
    @classmethod
    def entity_ids_are_unique(cls, devices: list[SimulatedDevice]) -> list[SimulatedDevice]:
        """Reject duplicate Home Assistant entities in a simulated home."""
        if len({device.entity_id for device in devices}) != len(devices):
            raise ValueError("device entity_ids must be unique")
        return devices

    @model_validator(mode="after")
    def references_are_within_the_household(self) -> "SimulatedHouseholdTemplate":
        """Ensure device and event references cannot cross household boundaries."""
        room_ids = {room.id for room in self.rooms}
        entity_ids = {device.entity_id for device in self.devices}
        unknown_rooms = {
            device.room_id
            for device in self.devices
            if device.room_id is not None and device.room_id not in room_ids
        }
        if unknown_rooms:
            raise ValueError(f"devices reference unknown rooms: {sorted(unknown_rooms)}")
        unknown_entities = {
            event.entity_id
            for scenario in self.scenarios
            for event in scenario.events
            if event.entity_id is not None and event.entity_id not in entity_ids
        }
        if unknown_entities:
            raise ValueError(
                f"scenario events reference unknown entities: {sorted(unknown_entities)}"
            )
        return self
