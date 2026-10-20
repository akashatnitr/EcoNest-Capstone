"""Starter household templates for safe, repeatable EcoNest simulation."""

from orchestrator.simulation.models import (
    SimulatedContext,
    SimulatedDevice,
    SimulatedHouseholdTemplate,
    SimulatedRoom,
    SimulatedScenario,
    SimulatedScenarioEvent,
)


def starter_households() -> list[SimulatedHouseholdTemplate]:
    """Return fresh templates so one simulation cannot mutate another household."""
    return [_city_apartment(), _family_home()]


def _city_apartment() -> SimulatedHouseholdTemplate:
    return SimulatedHouseholdTemplate(
        id="city_apartment",
        name="City Apartment",
        timezone="America/Chicago",
        resident_count=1,
        rooms=[
            SimulatedRoom(id="living_room", name="Living Room", kind="living_room"),
            SimulatedRoom(id="bedroom", name="Bedroom", kind="bedroom"),
        ],
        devices=[
            SimulatedDevice(
                entity_id="climate.living_room",
                name="Living Room Thermostat",
                room_id="living_room",
                domain="climate",
                state="cool",
                attributes={"temperature": 76, "current_temperature": 79},
                capabilities=["set_temperature"],
            ),
            SimulatedDevice(
                entity_id="light.living_room",
                name="Living Room Light",
                room_id="living_room",
                domain="light",
                state="on",
                capabilities=["turn_on", "turn_off", "set_brightness"],
            ),
            SimulatedDevice(
                entity_id="binary_sensor.living_room_motion",
                name="Living Room Motion",
                room_id="living_room",
                domain="binary_sensor",
                state="on",
                capabilities=["motion_detection"],
            ),
            SimulatedDevice(
                entity_id="sensor.home_power",
                name="Home Power",
                domain="sensor",
                state="1.4",
                attributes={"unit_of_measurement": "kW"},
                capabilities=["power_monitoring"],
            ),
        ],
        context=SimulatedContext(
            weather_summary="Warm and sunny",
            outdoor_temperature_f=88,
            household_mode="home",
        ),
        scenarios=[
            SimulatedScenario(
                id="warm_evening",
                name="Warm occupied evening",
                description="Resident is home in a warm living room and asks for cooling.",
                events=[
                    SimulatedScenarioEvent(
                        at_minutes=0,
                        event_type="occupancy",
                        description="Living room motion is active.",
                    ),
                    SimulatedScenarioEvent(
                        at_minutes=10,
                        event_type="state_change",
                        description="Indoor temperature rises due to warm weather.",
                        entity_id="climate.living_room",
                        state="cool",
                        attributes={"current_temperature": 81},
                    ),
                ],
                expected_outcomes=[
                    "Recommend a confirmed cooling adjustment for the occupied living room.",
                    "Do not change devices without confirmation.",
                ],
            )
        ],
    )


def _family_home() -> SimulatedHouseholdTemplate:
    return SimulatedHouseholdTemplate(
        id="family_home",
        name="Family Home",
        timezone="America/Chicago",
        resident_count=4,
        rooms=[
            SimulatedRoom(id="living_room", name="Living Room", kind="living_room"),
            SimulatedRoom(id="media_room", name="Media Room", kind="media_room"),
            SimulatedRoom(id="garage", name="Garage", kind="garage"),
        ],
        devices=[
            SimulatedDevice(
                entity_id="climate.living_room",
                name="Living Room Thermostat",
                room_id="living_room",
                domain="climate",
                state="cool",
                attributes={"temperature": 77, "current_temperature": 78},
                capabilities=["set_temperature"],
            ),
            SimulatedDevice(
                entity_id="climate.media_room",
                name="Media Room Thermostat",
                room_id="media_room",
                domain="climate",
                state="cool",
                attributes={"temperature": 78, "current_temperature": 80},
                capabilities=["set_temperature"],
            ),
            SimulatedDevice(
                entity_id="cover.garage_door",
                name="Garage Door",
                room_id="garage",
                domain="cover",
                state="closed",
                capabilities=["open", "close"],
            ),
            SimulatedDevice(
                entity_id="light.media_room",
                name="Media Room Dimmer",
                room_id="media_room",
                domain="light",
                state="off",
                capabilities=["turn_on", "turn_off", "set_brightness"],
            ),
            SimulatedDevice(
                entity_id="binary_sensor.media_room_motion",
                name="Media Room Motion",
                room_id="media_room",
                domain="binary_sensor",
                state="off",
                capabilities=["motion_detection"],
            ),
        ],
        context=SimulatedContext(
            weather_summary="Mild with evening rain",
            outdoor_temperature_f=68,
            household_mode="away",
            upcoming_events=["Family trip: Friday 8:00 AM through Sunday 7:00 PM"],
        ),
        scenarios=[
            SimulatedScenario(
                id="away_motion_alert",
                name="Unexpected motion while away",
                description="Motion begins in the media room while the household is away.",
                events=[
                    SimulatedScenarioEvent(
                        at_minutes=120,
                        event_type="state_change",
                        description="Motion appears in the media room during the trip.",
                        entity_id="binary_sensor.media_room_motion",
                        state="on",
                    )
                ],
                expected_outcomes=[
                    "Flag unexpected activity for the household.",
                    "Do not claim that a break-in is confirmed.",
                ],
            )
        ],
    )
