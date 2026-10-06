"""Tests for adaptive-learning state selection during HA ingestion."""

import asyncio
import pytest

from orchestrator.config import Settings
from orchestrator.core.ha_ingest import HomeAssistantIngestor, _is_sensor_state, _room_environments
from unittest.mock import AsyncMock
from orchestrator.mcp.models import ToolExecutionResult

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


@pytest.mark.asyncio
async def test_ha_ingestor_event_reaches_security_agent():
    from orchestrator.agents.orchestrator import AgentOrchestrator
    from orchestrator.agents.security_agent import SecurityAgent
    from orchestrator.core.event_dispatcher import EventDispatcher
    from orchestrator.core.home_events import classify_home_event

    class FakeSecurityLLM:
        def __init__(self):
            self.called = False

        async def generate(self, messages, temperature=0.7):
            self.called = True
            return "No immediate security escalation is recommended."

    llm = FakeSecurityLLM()
    security_agent = SecurityAgent(llm=llm)

    orchestrator = AgentOrchestrator(
        agents=[security_agent],
    )

    dispatcher = EventDispatcher(
        Settings(),
        submit_task=orchestrator.submit,
    )

    ingestor = HomeAssistantIngestor(
        Settings(),
        event_dispatcher=dispatcher,
    )

    previous = {
        "entity_id": "binary_sensor.front_door_motion",
        "state": "off",
        "last_updated": "2026-10-06T03:00:00+00:00",
        "attributes": {
            "friendly_name": "Front Door Motion",
            "device_class": "motion",
        },
    }

    current = {
        "entity_id": "binary_sensor.front_door_motion",
        "state": "on",
        "last_updated": "2026-10-06T03:01:00+00:00",
        "attributes": {
            "friendly_name": "Front Door Motion",
            "device_class": "motion",
        },
    }

    ingestor._last_event_states[previous["entity_id"]] = previous
    ingestor._fetch_states = AsyncMock(return_value=[current])
    ingestor._sync_statistics_if_due = AsyncMock(return_value=0)

    async def fake_store_states(states):
        transitions = ingestor._event_transitions(states)

        events = []
        for old_state, new_state in transitions:
            event = classify_home_event(old_state, new_state)
            if event is not None:
                events.append(event)

        for state in states:
            ingestor._last_event_states[state["entity_id"]] = state

        return (
            {
                "readings_inserted": 0,
                "home_events_recorded": len(events),
                "home_events_purged": 0,
                "sensor_readings_purged": 0,
            },
            [],
            events,
        )

    ingestor._store_states = fake_store_states

    result = await ingestor.run_once()

    assert result["events_dispatched"] == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]

    task_result = await orchestrator.get_result(task_id)

    assert task_result is not None
    assert task_result.success is True
    assert task_result.agent == "security"
    assert llm.called is True


# ------------------------------------------------------------------
# Level 2 autonomous event -> specialist -> MCP -> LLM integration
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_level2_security_event_reaches_agent_mcp_and_llm():
    from orchestrator.agents.security_agent import SecurityAgent
    from orchestrator.agents.orchestrator import AgentOrchestrator
    from orchestrator.core.event_dispatcher import EventDispatcher

    class FakeSecurityLLM:
        def __init__(self):
            self.called = False

        async def generate(self, prompt, temperature=0.7):
            self.called = True
            return "No immediate security escalation is recommended."

    llm = FakeSecurityLLM()
    agent = SecurityAgent(llm=llm)

    agent._observations_from_graph = AsyncMock(return_value=[])

    execute = AsyncMock(
        return_value=ToolExecutionResult(
            capability="ha_get_state",
            result={
                "entity_id": "binary_sensor.front_door_motion",
                "state": "on",
                "attributes": {
                    "friendly_name": "Front Door Motion",
                },
            },
        ),
    )
    agent.tool_executor.execute = execute
    agent.read_mcp_resource = AsyncMock(
        return_value={"recent_interactions": []}
    )

    orchestrator = AgentOrchestrator(agents=[agent])
    dispatcher = EventDispatcher(
        Settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "binary_sensor.front_door_motion",
        "event_type": "motion_detected",
        "previous_state": "off",
        "new_state": "on",
        "metadata": {
            "friendly_name": "Front Door Motion",
        },
    }

    assert await dispatcher.dispatch([event]) == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]
    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "security"
    assert llm.called is True

    ha_calls = [
        call for call in execute.await_args_list
        if call.args[0] == "ha_get_state"
    ]
    assert ha_calls
    assert ha_calls[0].args[1] == {
        "entity_id": "binary_sensor.front_door_motion",
    }

    assert not any(
        call.args[0] == "ha_call_service"
        for call in execute.await_args_list
    )


@pytest.mark.asyncio
async def test_level2_energy_event_reaches_agent_mcp_and_llm():
    from orchestrator.agents.energy_agent import EnergyAgent
    from orchestrator.agents.orchestrator import AgentOrchestrator
    from orchestrator.core.event_dispatcher import EventDispatcher

    class FakeEnergyLLM:
        def __init__(self):
            self.called = False

        async def generate_structured(
            self,
            messages,
            output_model,
            temperature=0.7,
        ):
            self.called = True
            return output_model(
                priority="HIGH",
                action="Investigate the unusually high appliance power draw.",
                reasoning="The current power is above the supplied baseline.",
            )

    llm = FakeEnergyLLM()
    agent = EnergyAgent(llm=llm)

    agent._mysql_energy_context = AsyncMock(
        return_value={"available": True}
    )
    agent._observations_from_graph = AsyncMock(return_value=[])
    agent._history_from_mysql = AsyncMock(return_value=[])

    execute = AsyncMock(
        return_value=ToolExecutionResult(
            capability="ha_get_state",
            result={
                "entity_id": "sensor.dishwasher_power",
                "state": "650",
                "attributes": {
                    "friendly_name": "Dishwasher Power",
                    "unit_of_measurement": "W",
                },
            },
        ),
    )
    agent.tool_executor.execute = execute

    orchestrator = AgentOrchestrator(agents=[agent])
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

    assert await dispatcher.dispatch([event]) == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]
    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "energy"
    assert llm.called is True

    ha_calls = [
        call for call in execute.await_args_list
        if call.args[0] == "ha_get_state"
    ]
    assert ha_calls
    assert ha_calls[0].args[1] == {
        "entity_id": "sensor.dishwasher_power",
    }

    assert not any(
        call.args[0] == "ha_call_service"
        for call in execute.await_args_list
    )


@pytest.mark.asyncio
async def test_level2_irrigation_event_reaches_agent_mcp_and_llm():
    from orchestrator.agents.irrigation_agent import IrrigationAgent
    from orchestrator.agents.orchestrator import AgentOrchestrator
    from orchestrator.core.event_dispatcher import EventDispatcher

    class FakeIrrigationLLM:
        def __init__(self):
            self.called = False

        async def generate(self, prompt, temperature=0.7):
            self.called = True
            return (
                "Recent conditions support reviewing the watering "
                "schedule before running the zone."
            )

    llm = FakeIrrigationLLM()
    agent = IrrigationAgent(llm=llm)

    agent._forecast_rows = AsyncMock(return_value=[])
    agent._irrigation_zones = AsyncMock(
        return_value=["switch.front_lawn_watering"]
    )
    agent._recent_irrigation_runs = AsyncMock(return_value=[])

    execute = AsyncMock(
        return_value=ToolExecutionResult(
            capability="ha_get_state",
            result={
                "entity_id": "switch.front_lawn_watering",
                "state": "on",
                "attributes": {
                    "friendly_name": "Front Lawn Watering",
                },
            },
        ),
    )
    agent.tool_executor.execute = execute

    orchestrator = AgentOrchestrator(agents=[agent])
    dispatcher = EventDispatcher(
        Settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "switch.front_lawn_watering",
        "event_type": "watering_started",
        "previous_state": "off",
        "new_state": "on",
        "metadata": {
            "friendly_name": "Front Lawn Watering",
        },
    }

    assert await dispatcher.dispatch([event]) == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]
    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "irrigation"
    assert llm.called is True

    ha_calls = [
        call for call in execute.await_args_list
        if call.args[0] == "ha_get_state"
    ]
    assert ha_calls
    assert ha_calls[0].args[1] == {
        "entity_id": "switch.front_lawn_watering",
    }

    assert not any(
        call.args[0] == "ha_call_service"
        for call in execute.await_args_list
    )


@pytest.mark.asyncio
async def test_level2_sensor_event_reaches_agent_mcp_and_llm():
    from orchestrator.agents.sensor_agent import SensorAgent
    from orchestrator.agents.orchestrator import AgentOrchestrator
    from orchestrator.core.event_dispatcher import EventDispatcher

    class FakeSensorLLM:
        def __init__(self):
            self.called = False

        async def generate(self, prompt, temperature=0.7):
            self.called = True
            return (
                "The sensor should be reviewed because its "
                "state is unavailable."
            )

    llm = FakeSensorLLM()
    agent = SensorAgent(llm=llm)

    agent._observations_from_graph = AsyncMock(return_value=[])
    agent.read_mcp_resource = AsyncMock(
        return_value={"recent_interactions": []}
    )

    execute = AsyncMock(
        return_value=ToolExecutionResult(
            capability="ha_get_state",
            result={
                "entity_id": "sensor.wifi_soil_sensor",
                "state": "unavailable",
                "attributes": {
                    "friendly_name": "WiFi Soil Sensor",
                },
            },
        ),
    )
    agent.tool_executor.execute = execute

    orchestrator = AgentOrchestrator(agents=[agent])
    dispatcher = EventDispatcher(
        Settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "sensor.wifi_soil_sensor",
        "event_type": "device_became_unavailable",
        "previous_state": "45",
        "new_state": "unavailable",
        "metadata": {
            "friendly_name": "WiFi Soil Sensor",
        },
    }

    assert await dispatcher.dispatch([event]) == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]
    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "sensor"
    assert llm.called is True
    assert result.data["llm_assessment"] is not None

    ha_calls = [
        call for call in execute.await_args_list
        if call.args[0] == "ha_get_state"
    ]
    assert ha_calls
    assert ha_calls[0].args[1] == {
        "entity_id": "sensor.wifi_soil_sensor",
    }

    assert not any(
        call.args[0] == "ha_call_service"
        for call in execute.await_args_list
    )
