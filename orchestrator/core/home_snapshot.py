from __future__ import annotations

from typing import Any


def build_home_snapshot(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact, human-readable snapshot from Home Assistant states."""

    people = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith(("person.", "device_tracker."))
    ]

    occupancy_status = _occupancy_status(people)

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
        "people": people[:10],
        "lights_on": lights_on[:15],
        "switches_on": switches_on[:15],
        "covers_open": covers_open,
        "active_motion": active_motion,
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


def _numeric_sensor_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = _entity_summary(item)
    summary["value"] = _float_or_none(item.get("state")) or 0.0
    return summary


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
