"""Tests for durable Home Assistant transition classification."""

from orchestrator.core.home_events import (
    ApplianceStatusCycleTracker,
    PowerCycleTracker,
    classify_home_event,
    is_event_candidate,
)


def _state(
    entity_id: str,
    state: str,
    *,
    device_class: str | None = None,
    temperature: float | None = None,
    observed_at: str = "2026-09-30T20:00:00+00:00",
) -> dict[str, object]:
    attributes: dict[str, object] = {}
    if device_class is not None:
        attributes["device_class"] = device_class
    if temperature is not None:
        attributes["temperature"] = temperature
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes,
        "last_changed": observed_at,
    }


def test_motion_transition_creates_one_durable_motion_event() -> None:
    event = classify_home_event(
        _state("binary_sensor.garage_motion", "off", device_class="motion"),
        _state("binary_sensor.garage_motion", "on", device_class="motion"),
    )

    assert event is not None
    assert event["event_type"] == "motion_detected"
    assert event["previous_state"] == "off"
    assert event["new_state"] == "on"


def test_watering_transitions_record_start_and_completion() -> None:
    off = _state("switch.back_lawn_manual_watering", "off")
    on = _state("switch.back_lawn_manual_watering", "on")

    started = classify_home_event(off, on)
    completed = classify_home_event(on, off)

    assert started is not None and started["event_type"] == "watering_started"
    assert completed is not None and completed["event_type"] == "watering_completed"


def test_thermostat_target_change_is_recorded_but_temperature_updates_are_not() -> None:
    event = classify_home_event(
        _state("climate.media_room", "cool", temperature=78),
        _state("climate.media_room", "cool", temperature=80),
    )

    assert event is not None
    assert event["event_type"] == "thermostat_target_changed"
    assert event["metadata"]["previous_target_temperature"] == 78
    assert event["metadata"]["target_temperature"] == 80
    assert classify_home_event(
        _state("sensor.media_room_temperature", "78.2", device_class="temperature"),
        _state("sensor.media_room_temperature", "78.3", device_class="temperature"),
    ) is None


def test_availability_transition_and_unchanged_state_handling() -> None:
    available = _state("binary_sensor.garage_motion", "off", device_class="motion")
    unavailable = _state("binary_sensor.garage_motion", "unavailable", device_class="motion")

    event = classify_home_event(available, unavailable)

    assert is_event_candidate(unavailable) is True
    assert event is not None and event["event_type"] == "device_became_unavailable"
    assert classify_home_event(available, available) is None


def test_power_cycle_requires_sustained_individual_appliance_usage() -> None:
    tracker = PowerCycleTracker(start_watts=100, end_watts=15, minimum_seconds=300)
    idle = _state(
        "sensor.dryer_power", "0", device_class="power", observed_at="2026-09-30T20:00:00+00:00"
    )
    active = _state(
        "sensor.dryer_power", "1803.3", device_class="power", observed_at="2026-09-30T20:01:00+00:00"
    )
    completed = _state(
        "sensor.dryer_power", "0", device_class="power", observed_at="2026-09-30T20:20:00+00:00"
    )

    assert is_event_candidate(active) is True
    assert tracker.observe(idle, active) == []
    events = tracker.observe(active, completed)

    assert [event["event_type"] for event in events] == [
        "appliance_cycle_started",
        "appliance_cycle_completed",
    ]
    assert events[1]["metadata"]["duration_seconds"] == 1_140


def test_power_cycle_ignores_short_and_aggregate_power_changes() -> None:
    tracker = PowerCycleTracker(start_watts=100, end_watts=15, minimum_seconds=300)
    idle = _state(
        "sensor.dryer_power", "0", device_class="power", observed_at="2026-09-30T20:00:00+00:00"
    )
    active = _state(
        "sensor.dryer_power", "800", device_class="power", observed_at="2026-09-30T20:01:00+00:00"
    )
    completed = _state(
        "sensor.dryer_power", "0", device_class="power", observed_at="2026-09-30T20:03:00+00:00"
    )
    aggregate = _state("sensor.breaker_power", "1000", device_class="power")

    assert tracker.observe(idle, active) == []
    assert tracker.observe(active, completed) == []
    assert is_event_candidate(aggregate) is False


def test_appliance_job_state_records_a_completed_cycle() -> None:
    tracker = ApplianceStatusCycleTracker()
    idle = _state(
        "sensor.dryer_job_state", "none", observed_at="2026-09-30T20:00:00+00:00"
    )
    drying = _state(
        "sensor.dryer_job_state", "drying", observed_at="2026-09-30T20:05:00+00:00"
    )
    finished = _state(
        "sensor.dryer_job_state", "finished", observed_at="2026-09-30T21:05:00+00:00"
    )

    started = tracker.observe(idle, drying)
    completed = tracker.observe(drying, finished)

    assert started[0]["event_type"] == "appliance_cycle_started"
    assert completed[0]["event_type"] == "appliance_cycle_completed"
    assert completed[0]["metadata"]["duration_seconds"] == 3_600
