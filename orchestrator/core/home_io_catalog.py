"""Verified I/O catalog for the standard Home I/O teaching house.

The addresses come from Real Games' published Home I/O memory-address table.
They apply to the simulator's built-in A--M zones, not to a real household.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HomeIOPoint:
    """A safe, named Home I/O point from the standard-house allow-list."""

    identifier: str
    zone: str
    label: str
    address: int
    memory_type: str
    kind: str
    data_type: str = "Bit"
    writable: bool = False
    momentary: bool = False


def _point(
    zone: str,
    slug: str,
    label: str,
    address: int,
    *,
    kind: str,
    writable: bool = False,
    momentary: bool = False,
) -> HomeIOPoint:
    return HomeIOPoint(
        identifier=f"homeio_{zone.lower()}_{slug}",
        zone=zone,
        label=label,
        address=address,
        memory_type="Output" if writable else "Input",
        kind=kind,
        writable=writable,
        momentary=momentary,
    )


def _float_point(
    zone: str,
    slug: str,
    label: str,
    address: int,
    *,
    memory_type: str,
    writable: bool = False,
) -> HomeIOPoint:
    """Create one explicit floating-point sensor or thermostat point."""
    return HomeIOPoint(
        identifier=f"homeio_{zone.lower()}_{slug}",
        zone=zone,
        label=label,
        address=address,
        memory_type=memory_type,
        kind="thermostat",
        data_type="Float",
        writable=writable,
    )


# Actuators are the complete bit-output catalog for zones A--M.  A device only
# responds in the desktop simulator after it has been put in External Mode.
_OUTPUTS: tuple[HomeIOPoint, ...] = (
    _point("A", "lights", "Lights", 0, kind="light", writable=True),
    *(_point("A", f"shade_{number}_{direction}", f"Roller shade {number} ({direction})", address, kind="shade", writable=True, momentary=True)
      for number, direction, address in ((1, "open", 1), (1, "close", 2), (2, "open", 3), (2, "close", 4), (3, "open", 5), (3, "close", 6), (4, "open", 7), (4, "close", 8))),
    _point("A", "heater", "Heater", 9, kind="heater", writable=True),
    _point("B", "lights_1", "Lights 1", 19, kind="light", writable=True),
    _point("B", "lights_2", "Lights 2", 20, kind="light", writable=True),
    _point("C", "lights", "Lights", 30, kind="light", writable=True),
    _point("D", "lights_1", "Lights 1", 40, kind="light", writable=True),
    _point("D", "lights_2", "Lights 2", 41, kind="light", writable=True),
    _point("D", "shade_open", "Roller shade (open)", 42, kind="shade", writable=True, momentary=True),
    _point("D", "shade_close", "Roller shade (close)", 43, kind="shade", writable=True, momentary=True),
    _point("D", "heater", "Heater", 44, kind="heater", writable=True),
    _point("E", "lights", "Lights", 54, kind="light", writable=True),
    _point("E", "shade_open", "Roller shade (open)", 55, kind="shade", writable=True, momentary=True),
    _point("E", "shade_close", "Roller shade (close)", 56, kind="shade", writable=True, momentary=True),
    _point("E", "heater", "Heater", 57, kind="heater", writable=True),
    _point("E", "siren", "Siren", 58, kind="siren", writable=True),
    _point("E", "alarm_arm", "Arm alarm", 59, kind="security", writable=True, momentary=True),
    _point("E", "alarm_disarm", "Disarm alarm", 60, kind="security", writable=True, momentary=True),
    _point("F", "lights_1", "Lights 1", 68, kind="light", writable=True),
    _point("F", "lights_2", "Lights 2", 69, kind="light", writable=True),
    _point("F", "shade_open", "Roller shade (open)", 70, kind="shade", writable=True, momentary=True),
    _point("F", "shade_close", "Roller shade (close)", 71, kind="shade", writable=True, momentary=True),
    _point("F", "garage_open", "Garage door (open)", 72, kind="door", writable=True, momentary=True),
    _point("F", "garage_close", "Garage door (close)", 73, kind="door", writable=True, momentary=True),
    _point("G", "lights", "Lights", 83, kind="light", writable=True),
    _point("G", "shade_open", "Roller shade (open)", 84, kind="shade", writable=True, momentary=True),
    _point("G", "shade_close", "Roller shade (close)", 85, kind="shade", writable=True, momentary=True),
    _point("G", "heater_1", "Heater 1", 86, kind="heater", writable=True),
    _point("G", "heater_2", "Heater 2", 87, kind="heater", writable=True),
    _point("H", "lights", "Lights", 97, kind="light", writable=True),
    _point("H", "shade_open", "Roller shade (open)", 98, kind="shade", writable=True, momentary=True),
    _point("H", "shade_close", "Roller shade (close)", 99, kind="shade", writable=True, momentary=True),
    _point("H", "heater", "Heater", 100, kind="heater", writable=True),
    _point("I", "lights_1", "Lights 1", 110, kind="light", writable=True),
    _point("I", "lights_2", "Lights 2", 111, kind="light", writable=True),
    _point("I", "heater", "Heater", 112, kind="heater", writable=True),
    _point("J", "lights", "Lights", 122, kind="light", writable=True),
    _point("J", "shade_open", "Roller shade (open)", 123, kind="shade", writable=True, momentary=True),
    _point("J", "shade_close", "Roller shade (close)", 124, kind="shade", writable=True, momentary=True),
    _point("J", "heater", "Heater", 125, kind="heater", writable=True),
    _point("K", "lights", "Lights", 135, kind="light", writable=True),
    _point("K", "heater", "Heater", 136, kind="heater", writable=True),
    _point("L", "lights", "Lights", 146, kind="light", writable=True),
    _point("L", "shade_open", "Roller shade (open)", 147, kind="shade", writable=True, momentary=True),
    _point("L", "shade_close", "Roller shade (close)", 148, kind="shade", writable=True, momentary=True),
    _point("L", "heater", "Heater", 149, kind="heater", writable=True),
    _point("M", "lights", "Lights", 159, kind="light", writable=True),
    _point("M", "shade_open", "Roller shade (open)", 160, kind="shade", writable=True, momentary=True),
    _point("M", "shade_close", "Roller shade (close)", 161, kind="shade", writable=True, momentary=True),
    _point("M", "heater", "Heater", 162, kind="heater", writable=True),
)

# These live inputs give EcoNest presence and safety feedback in each selected
# zone.  We intentionally do not expose wall-switch inputs as controls.
_INPUTS: tuple[HomeIOPoint, ...] = tuple(
    _point(zone, slug, label, address, kind=kind)
    for zone, slug, label, address, kind in (
        ("A", "motion", "Motion detector", 15, "motion"), ("A", "brightness", "Brightness sensor", 16, "sensor"), ("A", "smoke", "Smoke detector", 17, "smoke"),
        ("B", "motion", "Motion detector", 29, "motion"),
        ("D", "motion", "Motion detector", 56, "motion"), ("D", "brightness", "Brightness sensor", 57, "sensor"),
        ("E", "motion", "Motion detector", 80, "motion"), ("E", "brightness", "Brightness sensor", 81, "sensor"), ("E", "alarm_armed", "Alarm armed", 82, "security"),
        ("F", "motion", "Motion detector", 98, "motion"), ("F", "brightness", "Brightness sensor", 99, "sensor"), ("F", "garage_opened", "Garage door opened", 100, "door"), ("F", "garage_closed", "Garage door closed", 101, "door"), ("F", "garage_infrared", "Garage door infrared", 102, "door"),
        ("G", "motion", "Motion detector", 119, "motion"), ("G", "smoke", "Smoke detector", 120, "smoke"),
        ("H", "motion", "Motion detector", 138, "motion"), ("H", "brightness", "Brightness sensor", 139, "sensor"), ("H", "smoke", "Smoke detector", 140, "smoke"),
        ("I", "motion", "Motion detector", 153, "motion"),
        ("J", "motion", "Motion detector", 171, "motion"), ("J", "brightness", "Brightness sensor", 172, "sensor"), ("J", "smoke", "Smoke detector", 173, "smoke"),
        ("K", "motion", "Motion detector", 185, "motion"),
        ("L", "motion", "Motion detector", 205, "motion"), ("L", "brightness", "Brightness sensor", 206, "sensor"), ("L", "smoke", "Smoke detector", 207, "smoke"),
        ("M", "motion", "Motion detector", 222, "motion"), ("M", "brightness", "Brightness sensor", 223, "sensor"),
    )
)

# Room-temperature memory is in Kelvin. Thermostat set points are Home I/O
# Input floats in degrees Celsius and can be changed when that thermostat has
# been placed in External Mode. Addresses are from the official SDK table.
_THERMOSTATS: tuple[HomeIOPoint, ...] = tuple(
    _float_point(zone, "room_temperature", "Room temperature", 150 + index, memory_type="Memory")
    for index, zone in enumerate("ABCDEFGHIJKLM")
) + tuple(
    _float_point(zone, "thermostat_set_point", "Thermostat set point", address, memory_type="Input", writable=True)
    for zone, address in (("A", 2), ("D", 14), ("E", 26), ("G", 47), ("H", 59), ("I", 70), ("J", 82), ("K", 93), ("L", 105), ("M", 117))
)

STANDARD_HOME_IO_POINTS: tuple[HomeIOPoint, ...] = _OUTPUTS + _INPUTS + _THERMOSTATS


def standard_bridge_map() -> dict[str, dict[str, int | str | bool]]:
    """Return the bridge allow-list for the standard Home I/O house."""
    return {
        point.identifier: {
            "address": point.address,
            "memory_type": point.memory_type,
            "data_type": point.data_type,
            "writable": point.writable,
            "momentary": point.momentary,
        }
        for point in STANDARD_HOME_IO_POINTS
    }
