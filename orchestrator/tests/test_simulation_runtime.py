"""Tests for safe City Apartment interactive simulation controls."""

import pytest

from orchestrator.core.simulated_households import CASAS_ARUBA_NAME, CN_OBEE_NAME
from orchestrator.core.simulation_runtime import (
    _cn_obee_time_context,
    apply_cn_obee_manual_override,
    apply_casas_manual_override,
    apply_city_apartment_action,
    apply_simulation_action,
)


@pytest.mark.anyio
async def test_resident_leaves_only_changes_its_simulated_apartment() -> None:
    """A scenario action is contained to the selected household state."""
    first = await apply_city_apartment_action(301, "resident_leaves")
    second = await apply_city_apartment_action(302, "reset")
    assert first["summary"]["occupancy"] == "Away"
    assert second["summary"]["occupancy"] == "Home"


@pytest.mark.anyio
async def test_heat_wave_updates_temperature_and_timeline() -> None:
    """A heat event changes only the modeled state and records evidence."""
    await apply_city_apartment_action(303, "reset")
    state = await apply_city_apartment_action(303, "heat_wave")
    assert state["summary"]["indoor_temperature_f"] == 77
    assert "heat raised" in state["events"][0]


@pytest.mark.anyio
async def test_casas_replay_actions_are_isolated_from_the_city_apartment() -> None:
    """Dataset playback can branch safely without changing another household."""
    replay = await apply_simulation_action(401, CASAS_ARUBA_NAME, "resident_leaves")
    apartment = await apply_city_apartment_action(402, "reset")
    assert replay["summary"]["occupancy"] == "Away"
    assert "Replay branch" in replay["events"][0]
    assert apartment["summary"]["occupancy"] == "Home"


@pytest.mark.anyio
async def test_casas_arrival_and_departure_update_visual_overlays() -> None:
    """The cutaway visuals are driven by the same safe scenario state."""
    await apply_simulation_action(407, CASAS_ARUBA_NAME, "reset")
    arrived = await apply_simulation_action(407, CASAS_ARUBA_NAME, "resident_arrives")
    assert arrived["visual_overlays"] == {
        "resident_arriving": True,
        "resident_visible": True,
        "resident_room": "front_entry",
        "car_present": True,
    }
    departed = await apply_simulation_action(407, CASAS_ARUBA_NAME, "resident_leaves")
    assert departed["visual_overlays"] == {
        "resident_arriving": False,
        "resident_visible": False,
        "resident_room": None,
        "car_present": False,
    }


@pytest.mark.anyio
async def test_casas_resident_position_comes_from_an_active_motion_sensor() -> None:
    """The resident overlay follows active PIR evidence during replay steps."""
    await apply_simulation_action(408, CASAS_ARUBA_NAME, "reset")
    state = await apply_simulation_action(408, CASAS_ARUBA_NAME, "advance_ten_minutes")
    overlay = state["visual_overlays"]
    if overlay["resident_visible"]:
        active_motion_rooms = {
            device["room"]
            for device in state["devices"]
            if device["kind"] == "motion" and device["state"] == "active"
        }
        assert overlay["resident_room"] in active_motion_rooms


@pytest.mark.anyio
async def test_casas_manual_overrides_are_scoped_to_simulation_state() -> None:
    """Individual sensor and resident controls never touch live-home state."""
    await apply_simulation_action(409, CASAS_ARUBA_NAME, "reset")
    sensor = await apply_casas_manual_override(
        409, device_id="m001", device_state="active"
    )
    assert next(device for device in sensor["devices"] if device["id"] == "m001")["state"] == "active"
    assert sensor["visual_overlays"]["resident_room"] == "bedroom_1"
    assert next(room for room in sensor["rooms"] if room["id"] == "bedroom_1")["occupied"]
    resident = await apply_casas_manual_override(409, resident_room="kitchen")
    assert resident["visual_overlays"]["resident_room"] == "kitchen"
    assert next(room for room in resident["rooms"] if room["id"] == "kitchen")["occupied"]


@pytest.mark.anyio
async def test_casas_virtual_light_is_controlled_only_in_the_replay() -> None:
    """Virtual controls are explicit simulation overlays with simulated power."""
    await apply_simulation_action(403, CASAS_ARUBA_NAME, "reset")
    state = await apply_simulation_action(403, CASAS_ARUBA_NAME, "living_light_on")
    light = next(device for device in state["devices"] if device["id"] == "virtual_living_light")
    assert light["state"] == "on"
    assert state["summary"]["power_w"] == 9
    assert "Virtual living light" in state["events"][0]


@pytest.mark.anyio
async def test_casas_advance_moves_the_simulated_resident_through_activity() -> None:
    """Advancing time changes the simulated-live activity and sensor evidence."""
    await apply_simulation_action(404, CASAS_ARUBA_NAME, "reset")
    state = await apply_simulation_action(404, CASAS_ARUBA_NAME, "advance_ten_minutes")
    live = state["live_simulation"]
    assert live["simulated_at"].startswith("2026-")
    assert live["mode"].startswith("Synthetic 2026")
    assert "Synthetic continuation" in live["provenance"]
    assert state["summary"]["active_rooms"] >= 0


@pytest.mark.anyio
async def test_cn_obee_virtual_control_preserves_source_backed_appliance_state() -> None:
    """Counterfactual controls never overwrite the replay's recorded appliances."""
    await apply_simulation_action(405, CN_OBEE_NAME, "reset")
    state = await apply_simulation_action(405, CN_OBEE_NAME, "living_light_on")
    light = next(device for device in state["devices"] if device["id"] == "virtual_living_light")
    fridge = next(device for device in state["devices"] if device["id"] == "fridge")
    assert light["state"] == "on"
    assert fridge["detail"] == "51.01 W · recorded appliance channel"
    assert "source readings remain unchanged" in state["events"][0]


@pytest.mark.anyio
async def test_cn_obee_scenarios_update_visible_simulated_state() -> None:
    """CN-OBEE scenario controls change the safe replay overlay visibly."""
    await apply_simulation_action(406, CN_OBEE_NAME, "reset")
    arrived = await apply_simulation_action(406, CN_OBEE_NAME, "resident_arrives")
    assert arrived["summary"]["active_rooms"] == 2
    assert next(room for room in arrived["rooms"] if room["id"] == "cloakroom")["occupied"]

    heated = await apply_simulation_action(406, CN_OBEE_NAME, "heat_wave")
    temperature = next(
        device for device in heated["devices"] if device["id"] == "living_room_temperature"
    )
    assert "counterfactual heat adjustment" in temperature["detail"]

    advanced = await apply_simulation_action(406, CN_OBEE_NAME, "advance_ten_minutes")
    live = advanced["live_simulation"]
    assert live["simulated_at"].startswith("2026-")
    assert live["mode"] == "Live-paced 2026 CN-OBEE simulation"


@pytest.mark.anyio
async def test_cn_obee_manual_occupancy_override_moves_resident_overlay() -> None:
    """CN-OBEE room-presence overrides drive only its visual simulation state."""
    await apply_simulation_action(410, CN_OBEE_NAME, "reset")
    sensor = await apply_cn_obee_manual_override(
        410, device_id="home_office_occupancy", device_state="active"
    )
    assert sensor["visual_overlays"]["resident_room"] == "home_office"
    assert sensor["visual_overlays"]["resident_visible"] is True
    assert next(room for room in sensor["rooms"] if room["id"] == "home_office")["occupied"]
    resident = await apply_cn_obee_manual_override(410, resident_room="away")
    assert resident["summary"]["occupancy"] == "Away"
    assert resident["visual_overlays"]["resident_visible"] is False


def test_cn_obee_time_context_is_not_presented_as_a_dataset_activity() -> None:
    """The source has occupancy labels, not an activity taxonomy."""
    assert _cn_obee_time_context(5) == "Night-time at home"
    assert _cn_obee_time_context(8) == "Morning at home"
    assert _cn_obee_time_context(17) == "Evening at home"
    assert _cn_obee_time_context(22) == "Night-time at home"
