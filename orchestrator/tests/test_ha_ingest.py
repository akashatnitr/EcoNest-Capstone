"""Tests for adaptive-learning state selection during HA ingestion."""

import asyncio
import pytest

from orchestrator.config import Settings
from orchestrator.core.ha_ingest import HomeAssistantIngestor, _is_sensor_state, _room_environments
from unittest.mock import AsyncMock

def test_adaptive_ingestion_keeps_climate_weather_and_irrigation_states():
    assert _is_sensor_state({"entity_id": "climate.media_room", "state": "cool"})
    assert _is_sensor_state({"entity_id": "weather.forecast_home", "state": "sunny"})
    assert _is_sensor_state({"entity_id": "valve.front_lawn", "state": "closed"})
    assert _is_sensor_state({"entity_id": "switch.front_lawn_watering", "state": "off"})
    assert not _is_sensor_state({"entity_id": "switch.garage", "state": "off"})
    assert not _is_sensor_state({"entity_id": "climate.media_room", "state": "unavailable"})


def test_room_environments_uses_temperature_and_humidity_states_without_registry():
    environments = _room_environments(
        [
            {
                "entity_id": "sensor.temperature",
                "state": "72.4",
                "attributes": {"device_class": "temperature"},
            },
            {
                "entity_id": "sensor.humidity",
                "state": "45",
                "attributes": {"device_class": "humidity"},
            },
        ],
        None,
    )

    assert environments == {"home_assistant": {"temperature": 72.4, "humidity": 45.0}}


def test_ingestor_waits_for_a_prior_state_before_emitting_events():
    ingestor = HomeAssistantIngestor(Settings())
    initial = {"entity_id": "binary_sensor.garage_motion", "state": "off", "last_updated": "one"}
    changed = {"entity_id": "binary_sensor.garage_motion", "state": "on", "last_updated": "two"}

    assert ingestor._event_transitions([initial]) == []
    ingestor._last_event_states["binary_sensor.garage_motion"] = initial
    assert ingestor._event_transitions([changed]) == [(initial, changed)]

import pytest


@pytest.mark.asyncio
async def test_run_once_dispatches_normalized_events():
    dispatcher = AsyncMock()
    dispatcher.dispatch.return_value = 1

    ingestor = HomeAssistantIngestor(
        Settings(),
        event_dispatcher=dispatcher,
    )

    normalized_event = {
        "entity_id": "sensor.dishwasher_power",
        "event_type": "energy_anomaly_detected",
        "previous_state": "120",
        "new_state": "650",
        "metadata": {
            "power_watts": 650.0,
            "baseline_watts": 100.0,
            "friendly_name": "Dishwasher Power",
        },
    }

    ingestor._store_states = AsyncMock(
        return_value=(
            {
                "readings_inserted": 0,
                "home_events_recorded": 1,
                "home_events_purged": 0,
                "sensor_readings_purged": 0,
            },
            [],
            [normalized_event],
        )
    )

    ingestor._fetch_states = AsyncMock(return_value=[])
    ingestor._sync_statistics_if_due = AsyncMock(return_value=0)

    result = await ingestor.run_once()

    dispatcher.dispatch.assert_awaited_once_with([normalized_event])
    assert result["events_dispatched"] == 1


@pytest.mark.asyncio
async def test_autonomous_energy_event_reaches_agent_and_llm():
    from orchestrator.agents.energy_agent import EnergyAgent
    from orchestrator.agents.orchestrator import AgentOrchestrator
    from orchestrator.core.event_dispatcher import EventDispatcher

    class FakeEnergyLLM:
        def __init__(self):
            self.called = False

        async def generate_structured(
            self, messages, output_model, temperature=0.7
        ):
            self.called = True
            return output_model(
                priority="HIGH",
                action="Investigate the unusually high appliance power draw.",
                reasoning="The appliance power is significantly above its baseline.",
            )

    llm = FakeEnergyLLM()
    energy_agent = EnergyAgent(llm=llm)

    # Keep this integration test deterministic: no real ArcadeDB/MySQL calls.
    async def fake_context(task):
        return {
            "current_hour": 18,
            "source": "ha_event_dispatcher",
            "trigger": "energy_anomaly_detected",
            "mysql": {"available": False},
        }

    async def fake_graph(task):
        return []

    async def fake_history(task):
        return []

    energy_agent._build_context = fake_context
    energy_agent._observations_from_graph = fake_graph
    energy_agent._history_from_mysql = fake_history

    orchestrator = AgentOrchestrator(
        agents=[energy_agent],
    )

    dispatcher = EventDispatcher(
        Settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "sensor.dishwasher_power",
        "event_type": "energy_anomaly_detected",
        "previous_state": "120",
        "new_state": "650",
        "metadata": {
            "power_watts": 650.0,
            "baseline_watts": 100.0,
            "friendly_name": "Dishwasher Power",
        },
    }

    dispatched = await dispatcher.dispatch([event])

    assert dispatched == 1

    # The dispatcher submits asynchronously, so wait for the task to finish.
    await asyncio.sleep(0.05)

    assert llm.called is True