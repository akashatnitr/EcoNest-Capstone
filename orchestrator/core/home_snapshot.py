from __future__ import annotations

from typing import Any

import httpx

from orchestrator.config import Settings


_DEFAULT_INFERRED_ACTIVITY_DIMMER_ENTITY_IDS = frozenset(
    {
        "light.bedroom_2_light_1",
        "light.hallway_kids_light_1",
        "light.master_bedroom_light_1",
        "light.upstairs_media_light_1",
        "light.dimmer_switch_light_1",
        "light.dimmer_switch_2_light_1",
    }
)


async def fetch_home_snapshot(settings: Settings) -> dict[str, Any]:
    """Read a compact current snapshot from Home Assistant."""
    if not settings.HA_TOKEN:
        return {
            "type": "snapshot",
            "available": False,
            "warnings": ["Home Assistant is not configured"],
        }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.HA_URL.rstrip('/')}/api/states",
                headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return {
            "type": "snapshot",
            "available": False,
            "warnings": ["Home Assistant snapshot is unavailable"],
        }
    data = response.json()
    states = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return {
        "type": "snapshot",
        "available": True,
        **build_home_snapshot(
            states,
            inferred_activity_entity_ids=configured_dimmer_activity_entity_ids(settings),
            inferred_activity_timeout_minutes=settings.INFERRED_ACTIVITY_DIMMER_TIMEOUT_MINUTES,
        ),
    }


def build_home_snapshot(
    states: list[dict[str, Any]],
    *,
    inferred_activity_entity_ids: set[str] | None = None,
    inferred_activity_timeout_minutes: int = 10,
) -> dict[str, Any]:
    """Build a compact, human-readable snapshot from Home Assistant states."""

    people = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("person.")
    ]
    tracked_devices = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("device_tracker.")
    ]

    occupancy_status = _occupancy_status([*people, *tracked_devices])

    temperature_readings = sorted(
        [
            _temperature_summary(item)
            for item in states
            if str(item.get("entity_id", "")).startswith("climate.")
            and _attribute_number(item, "current_temperature") is not None
        ],
        key=lambda item: str(item["name"]),
    )
    indoor_temperatures = [
        float(reading["state"])
        for reading in temperature_readings
    ]
    current_indoor_temperature_f = (
        round(sum(indoor_temperatures) / len(indoor_temperatures), 1)
        if indoor_temperatures
        else None
    )

    lights_on = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("light.")
        and str(item.get("state", "")).lower() == "on"
    ]

    switches_on = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("switch.")
        and str(item.get("state", "")).lower() == "on"
    ]

    covers_open = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("cover.")
        and str(item.get("state", "")).lower() not in {"closed", "closing"}
    ]

    active_motion = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("binary_sensor.")
        and "motion" in _entity_text(item)
        and str(item.get("state", "")).lower() == "on"
    ]
    inferred_room_activity = [
        _inferred_activity_summary(item, inferred_activity_timeout_minutes)
        for item in states
        if str(item.get("entity_id", ""))
        in (inferred_activity_entity_ids or _DEFAULT_INFERRED_ACTIVITY_DIMMER_ENTITY_IDS)
        and str(item.get("state", "")).lower() == "on"
    ]

    power_now = sorted(
        [
            _numeric_sensor_summary(item)
            for item in states
            if "power_minute_average" in str(item.get("entity_id", ""))
        ],
        key=lambda item: item["value"],
        reverse=True,
    )[:8]

    energy_today = sorted(
        [
            _numeric_sensor_summary(item)
            for item in states
            if "energy_today" in str(item.get("entity_id", ""))
        ],
        key=lambda item: item["value"],
        reverse=True,
    )[:8]

    return {
        "occupancy_status": occupancy_status,
        "current_indoor_temperature_f": current_indoor_temperature_f,
        "temperature_readings": temperature_readings,
        "people": people[:10],
        "tracked_devices": tracked_devices[:10],
        "lights_on": lights_on[:15],
        "switches_on": switches_on[:15],
        "covers_open": covers_open,
        "active_motion": active_motion,
        "inferred_room_activity": inferred_room_activity,
        "inferred_activity_timeout_minutes": inferred_activity_timeout_minutes,
        "top_power_now_w": power_now,
        "top_energy_today_kwh": energy_today,
    }


def _entity_summary(item: dict[str, Any]) -> dict[str, Any]:
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}

    return {
        "entity_id": item.get("entity_id"),
        "name": attributes.get("friendly_name") or item.get("entity_id"),
        "state": item.get("state"),
    }


def _inferred_activity_summary(
    item: dict[str, Any], timeout_minutes: int
) -> dict[str, Any]:
    """Describe current room activity inferred from a motion-enabled dimmer light."""
    summary = _entity_summary(item)
    name = str(summary["name"])
    summary["name"] = _room_name_from_dimmer(name)
    summary["state"] = f"active now · inferred from dimmer light ({timeout_minutes}-minute timeout)"
    summary["source"] = "dimmer_light"
    return summary


def _room_name_from_dimmer(name: str) -> str:
    """Turn the integration's light label into the room-facing activity label."""
    lowered = name.lower()
    for suffix in (" lights light 1", " dimmer switch light 1"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def configured_dimmer_activity_entity_ids(settings: Settings) -> set[str]:
    """Read the locally configurable set of dimmers used as activity proxies."""
    entity_ids = {
        value.strip()
        for value in settings.INFERRED_ACTIVITY_DIMMER_ENTITY_IDS.split(",")
        if value.strip()
    }
    return entity_ids or set(_DEFAULT_INFERRED_ACTIVITY_DIMMER_ENTITY_IDS)


def _numeric_sensor_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = _entity_summary(item)
    summary["value"] = _float_or_none(item.get("state")) or 0.0
    return summary


def _temperature_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = _entity_summary(item)
    summary["state"] = _attribute_number(item, "current_temperature")
    return summary


def _attribute_number(item: dict[str, Any], attribute: str) -> float | None:
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        return None
    return _float_or_none(attributes.get(attribute))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entity_text(item: dict[str, Any]) -> str:
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}

    return (
        f"{item.get('entity_id', '')} "
        f"{attributes.get('friendly_name', '')}"
    ).lower()


def _occupancy_status(people: list[dict[str, Any]]) -> str:
    if not people:
        return "unknown"

    if any(str(person.get("state", "")).lower() == "home" for person in people):
        return "home"

    if all(
        str(person.get("state", "")).lower() in {"not_home", "away"}
        for person in people
    ):
        return "away"

    return "mixed"
