"""Read-only answers over EcoNest's retained important household events."""

from __future__ import annotations

import json
from typing import Any

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.agents.history_analysis import HistoryAnalysisPlan, RoomComfortQueryPlan


class EventHistoryAgent(BaseAgent):
    """Summarize compact Home Assistant event history without controlling devices."""

    name = "event_history"
    tools = ["query_mysql"]
    permissions = ["device:read", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        """Handle explicit requests for retained household events."""
        return task.payload.get("type") in {"event_history", "home_data"}

    async def run(self, task: Task) -> Result:
        """Retrieve the latest bounded set of meaningful household events."""
        plan = _analysis_plan(task.payload)
        if plan is not None:
            return await self._run_analysis(task, plan)
        comfort_plan = _room_comfort_plan(task.payload)
        if comfort_plan is not None:
            return await self._run_room_comfort_query(task, comfort_plan)
        cycle_search = _cycle_search(task.payload)
        if cycle_search:
            return await self._last_appliance_cycle(task, cycle_search)
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT he.occurred_at, he.event_type, he.entity_id, "
                    "he.previous_state, he.new_state, he.metadata, "
                    "r.name AS room, d.name AS device "
                    "FROM home_events he "
                    "LEFT JOIN devices d ON d.id = he.device_id "
                    "LEFT JOIN rooms r ON r.id = he.room_id "
                    "ORDER BY he.occurred_at DESC, he.id DESC LIMIT 20"
                )
            },
        )
        events = result.result if result.success and isinstance(result.result, list) else []
        rows = [event for event in events if isinstance(event, dict)]
        if not rows:
            answer = (
                "No important household events have been retained yet. "
                "EcoNest begins collecting new events after the event-history layer starts."
            )
        else:
            answer = (
                f"EcoNest found {len(rows)} recent important household event"
                f"{'s' if len(rows) != 1 else ''}."
            )
        return Result(
            success=result.success,
            data={"answer": answer, "events": rows},
            message="Home event history retrieved",
        )

    async def _run_analysis(self, task: Task, plan: HistoryAnalysisPlan) -> Result:
        """Execute Gemma's bounded plan using parameterized read-only data access."""
        filters = ["he.event_type = :event_type"] if plan.scope == "appliance_cycles" else ["1 = 1"]
        params: dict[str, Any] = {"limit": 500}
        if plan.scope == "appliance_cycles":
            params["event_type"] = "appliance_cycle_completed"
        if plan.subject:
            filters.append(
                "(LOWER(he.entity_id) LIKE :search OR LOWER(COALESCE(d.name, '')) LIKE :search "
                "OR LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(he.metadata, '$.friendly_name')), '')) LIKE :search)"
            )
            params["search"] = f"%{plan.subject.lower()}%"
        if plan.period_days is not None:
            filters.append("he.occurred_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL :period_days DAY)")
            params["period_days"] = plan.period_days
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT he.occurred_at, he.event_type, he.entity_id, he.metadata, "
                    "d.name AS device, r.name AS room FROM home_events he "
                    "LEFT JOIN devices d ON d.id = he.device_id "
                    "LEFT JOIN rooms r ON r.id = he.room_id WHERE "
                    + " AND ".join(filters)
                    + " ORDER BY he.occurred_at DESC, he.id DESC LIMIT :limit"
                ),
                "params": params,
            },
        )
        rows = result.result if result.success and isinstance(result.result, list) else []
        events = [event for event in rows if isinstance(event, dict)]
        answer = _analysis_answer(plan, events)
        return Result(
            success=result.success,
            data={"answer": answer, "events": events, "plan": plan.model_dump()},
            message="Gemma-planned historical analysis completed",
        )

    async def _run_room_comfort_query(self, task: Task, plan: RoomComfortQueryPlan) -> Result:
        """Retrieve a latest room condition selected by Gemma through a safe plan."""
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT r.name AS room, co.observed_at, co.target_temperature, "
                    "co.current_temperature, co.humidity_percent, co.hvac_mode "
                    "FROM comfort_observations co JOIN rooms r ON r.id = co.room_id "
                    "WHERE LOWER(r.name) LIKE :room ORDER BY co.observed_at DESC LIMIT 1"
                ),
                "params": {"room": f"%{plan.room.lower()}%"},
            },
        )
        rows = result.result if result.success and isinstance(result.result, list) else []
        observations = [row for row in rows if isinstance(row, dict)]
        answer = _room_comfort_answer(plan, observations[0] if observations else None)
        return Result(
            success=result.success,
            data={"answer": answer, "events": [], "room_state": observations, "plan": plan.model_dump()},
            message="Gemma-planned current room-state query completed",
        )

    async def _last_appliance_cycle(self, task: Task, search: str) -> Result:
        """Answer a factual question about the latest completed appliance cycle."""
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT he.occurred_at, he.event_type, he.entity_id, he.metadata, "
                    "d.name AS device, r.name AS room "
                    "FROM home_events he "
                    "LEFT JOIN devices d ON d.id = he.device_id "
                    "LEFT JOIN rooms r ON r.id = he.room_id "
                    "WHERE he.event_type = :event_type AND ("
                    "LOWER(he.entity_id) LIKE :search OR "
                    "LOWER(COALESCE(d.name, '')) LIKE :search OR "
                    "LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(he.metadata, '$.friendly_name')), '')) LIKE :search"
                    ") ORDER BY he.occurred_at DESC, he.id DESC LIMIT 1"
                ),
                "params": {"event_type": "appliance_cycle_completed", "search": f"%{search}%"},
            },
        )
        rows = result.result if result.success and isinstance(result.result, list) else []
        events = [event for event in rows if isinstance(event, dict)]
        if not events:
            return Result(
                success=result.success,
                data={
                    "answer": (
                        f"No completed {search} cycles have been retained yet. "
                        "EcoNest records a cycle only after it observes the appliance turn on "
                        "and later return to low power."
                    ),
                    "events": [],
                },
                message="Appliance-cycle history retrieved",
            )

        event = events[0]
        metadata = _metadata(event.get("metadata"))
        subject = str(event.get("device") or metadata.get("friendly_name") or search)
        completed_at = str(event.get("occurred_at") or "an unavailable time")
        duration_seconds = metadata.get("duration_seconds")
        duration = _duration_text(duration_seconds)
        answer = f"The last completed {subject} cycle ended at {completed_at}."
        if duration:
            answer += f" It lasted about {duration}."
        return Result(
            success=result.success,
            data={"answer": answer, "events": events},
            message="Appliance-cycle history retrieved",
        )


def _cycle_search(payload: dict[str, Any]) -> str | None:
    """Return a bounded appliance search phrase supplied by safe command routing."""
    if payload.get("history_kind") != "appliance_cycle":
        return None
    value = " ".join(str(payload.get("event_search") or "").lower().split())
    return value[:80] or None


def _analysis_plan(payload: dict[str, Any]) -> HistoryAnalysisPlan | None:
    """Accept only the bounded plan schema emitted by the command interpreter."""
    raw_plan = payload.get("history_analysis")
    if not isinstance(raw_plan, dict):
        return None
    try:
        return HistoryAnalysisPlan.model_validate(raw_plan)
    except ValueError:
        return None


def _room_comfort_plan(payload: dict[str, Any]) -> RoomComfortQueryPlan | None:
    """Accept only the bounded current-room query emitted by the interpreter."""
    raw_plan = payload.get("room_comfort_query")
    if not isinstance(raw_plan, dict):
        return None
    try:
        return RoomComfortQueryPlan.model_validate(raw_plan)
    except ValueError:
        return None


def _analysis_answer(plan: HistoryAnalysisPlan, events: list[dict[str, Any]]) -> str:
    """Calculate the requested statistic from retrieved evidence, never from model guesses."""
    subject = plan.subject or "matching home"
    period = f" over the past {plan.period_days} days" if plan.period_days else ""
    if not events:
        return f"No retained {subject} events match this analysis{period}."
    durations = [
        int(value)
        for event in events
        if (value := _metadata(event.get("metadata")).get("duration_seconds")) is not None
        and _is_integer(value)
    ]
    if plan.operation == "latest":
        return f"The latest matching {subject} event was at {events[0].get('occurred_at', 'an unavailable time')}."
    if plan.operation == "count":
        return f"EcoNest found {len(events)} matching {subject} events{period}."
    if plan.operation == "list":
        return f"EcoNest found {len(events)} matching {subject} events{period}; the latest are shown below."
    if not durations:
        return f"EcoNest found matching {subject} events{period}, but none include a recorded duration."
    if plan.operation == "average_duration":
        average = round(sum(durations) / len(durations))
        return f"The average duration of {len(durations)} matching {subject} events{period} was {_duration_text(average)}."
    return f"The total duration of {len(durations)} matching {subject} events{period} was {_duration_text(sum(durations))}."


def _is_integer(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _room_comfort_answer(plan: RoomComfortQueryPlan, row: dict[str, Any] | None) -> str:
    """Format only the retrieved room values; no temperature is model-invented."""
    if row is None:
        return f"No retained comfort observation was found for {plan.room}."
    labels = {
        "target_temperature": "comfort target",
        "current_temperature": "current measured temperature",
        "humidity": "humidity",
        "hvac_mode": "HVAC mode",
    }
    keys = {
        "target_temperature": "target_temperature",
        "current_temperature": "current_temperature",
        "humidity": "humidity_percent",
        "hvac_mode": "hvac_mode",
    }
    value = row.get(keys[plan.metric])
    if value is None:
        return f"EcoNest has a recent observation for {row.get('room', plan.room)}, but its {labels[plan.metric]} is unavailable."
    suffix = "°F" if plan.metric in {"target_temperature", "current_temperature"} else "%" if plan.metric == "humidity" else ""
    return f"The latest recorded {labels[plan.metric]} for {row.get('room', plan.room)} is {value}{suffix}."


def _metadata(value: Any) -> dict[str, Any]:
    """Handle MySQL JSON values returned as either mappings or strings."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _duration_text(value: Any) -> str | None:
    """Turn a recorded duration into short reader-friendly text."""
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return None
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = round(seconds / 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
