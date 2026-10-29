"""Tests for the initial safe simulated household scenario."""

from orchestrator.core.simulated_households import (
    CASAS_ARUBA_NAME,
    CN_OBEE_NAME,
    HOME_IO_NAME,
    casas_aruba_state,
    cn_obee_state,
    city_apartment_state,
    home_io_state,
    simulated_household_state,
)


def test_city_apartment_has_positioned_devices_in_known_rooms() -> None:
    """The visual scenario must not contain devices outside its declared rooms."""
    state = city_apartment_state()
    rooms = {room["id"] for room in state["rooms"]}
    assert len(state["rooms"]) == 4
    assert len(state["devices"]) >= 6
    assert all(device["room"] in rooms for device in state["devices"])
    assert all(0 <= device["x"] <= 100 and 0 <= device["y"] <= 100 for device in state["devices"])


def test_city_apartment_state_isolated_between_reads() -> None:
    """Browser requests cannot mutate the next scenario response in memory."""
    first = city_apartment_state()
    first["rooms"][0]["name"] = "Changed"
    assert city_apartment_state()["rooms"][0]["name"] == "Bedroom"


def test_casas_replay_keeps_source_and_recorded_sensor_context() -> None:
    """Dataset-backed replays expose their provenance and usable sensor events."""
    state = casas_aruba_state()
    assert state["source"]["license"] == "CC BY 4.0"
    assert state["source"]["url"].startswith("https://zenodo.org/")
    assert any(device["id"] == "m004" for device in state["devices"])
    assert next(device for device in state["devices"] if device["id"] == "m015")[
        "label"
    ] == "Kitchen · M015 motion sensor"
    assert next(device for device in state["devices"] if device["id"] == "t003")[
        "label"
    ] == "Kitchen · T003 temperature sensor"
    assert any("Bed_to_Toilet" in event for event in state["events"])
    assert len(state["devices"]) == 43  # 40 CASAS sensors + 3 virtual test actuators
    assert {room["name"] for room in state["rooms"]} >= {
        "Bedroom 1", "Bedroom 2", "Kitchen", "Office", "Garage entry",
    }
    assert "31 PIR motion" in state["dataset_overview"]["sensors"]
    assert simulated_household_state(CASAS_ARUBA_NAME) == state


def test_cn_obee_replay_exposes_its_actual_room_and_power_schema() -> None:
    """CN-OBEE retains source provenance without treating it as a live home."""
    state = cn_obee_state()
    assert state["source"]["license"] == "CC0"
    assert state["live_simulation"]["source_timestamp"].startswith("2021-")
    assert "2021" not in state["dataset_overview"]["source_range"]
    assert {room["name"] for room in state["rooms"]} == {
        "Cloakroom", "Home office", "Kitchen", "Living room",
        "Master bedroom", "Secondary bedroom",
    }
    assert any(device["id"] == "fridge" for device in state["devices"])
    assert any(device["id"] == "living_room_occupancy" for device in state["devices"])
    assert simulated_household_state(CN_OBEE_NAME) == state


def test_home_io_test_home_is_explicitly_isolated_demo_state() -> None:
    """Home I/O is present before a licensed desktop bridge is attached."""
    state = home_io_state()
    assert state["source"]["kind"] == "simulator"
    assert "bridge disconnected" in state["live_simulation"]["mode"]
    assert {room["name"] for room in state["rooms"]} >= {"A", "F", "M"}
    assert any(device["id"] == "homeio_a_lights" for device in state["devices"])
    assert simulated_household_state(HOME_IO_NAME) == state
