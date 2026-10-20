"""Turn upcoming household context into a resident-facing home plan."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from orchestrator.llm.client import LLMClient
from orchestrator.llm.models import LLMMessage


class HouseholdPlanItem(BaseModel):
    """One clear recommendation or expected household pattern."""

    label: str = Field(min_length=1, max_length=80)
    recommendation: str = Field(min_length=1, max_length=240)


class HouseholdPlanAction(BaseModel):
    """One resident-editable device action, never executed by plan generation."""

    entity_id: str = Field(min_length=3, max_length=160)
    entity_name: str = Field(min_length=1, max_length=120)
    action: Literal["turn_on", "turn_off", "set_temperature", "open", "close"]
    temperature: float | None = Field(default=None, ge=50, le=95)


class HouseholdPlan(BaseModel):
    """A bounded plan for an away or hosting context window."""

    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=320)
    settings: list[HouseholdPlanItem] = Field(min_length=1, max_length=6)
    expected_patterns: list[str] = Field(min_length=1, max_length=6)
    watch_for: list[str] = Field(min_length=1, max_length=6)
    actions: list[HouseholdPlanAction] = Field(default_factory=list, max_length=12)


async def create_household_plan(
    *,
    calendar: dict[str, Any],
    gmail: dict[str, Any],
    weather: dict[str, Any],
    snapshot: dict[str, Any],
    preferences: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a Gemma-backed plan with a clear local fallback.

    Only derived Calendar/Gmail categories and compact current-house facts are
    provided to the model. Raw email and calendar text never enter the prompt.
    """
    selected = _select_context(calendar, gmail, snapshot)

    temperature_setting = _temperature_guidance(weather, selected["mode"])
    fallback = _fallback_plan(selected["mode"], snapshot, temperature_setting)
    preference = (preferences or {}).get(selected["mode"])
    prompt = _plan_prompt(selected, weather, snapshot, preference)
    client = LLMClient()
    try:
        generated = await client.generate_structured(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "Use recommendation language only. Never state or imply that a device "
                        "has already been changed, is currently in a requested state, or will "
                        "automatically be changed."
                    ),
                ),
                LLMMessage(role="user", content=prompt),
            ],
            HouseholdPlan,
            temperature=0.2,
        )
        if not _is_recommendation_only(generated):
            raise ValueError("Generated plan included unsupported device-state claims")
        if not _matches_selected_mode(generated, selected["mode"]):
            raise ValueError("Generated plan did not match the selected household context")
        plan = _with_plan_actions(
            _with_temperature_guidance(generated, selected["mode"], temperature_setting),
            snapshot,
            selected["mode"],
            temperature_setting,
            preference,
        ).model_dump()
        plan_source = "Gemma"
    except Exception:
        plan = _with_plan_actions(
            fallback, snapshot, selected["mode"], temperature_setting, preference
        ).model_dump()
        plan_source = "EcoNest plan"
    finally:
        await client.close()

    return {
        "available": True,
        "source": plan_source,
        "mode": selected["mode"],
        "window": {
            "source": selected["source"],
            "starts_at": selected["starts_at"],
            "ends_at": selected["ends_at"],
        },
        "plan": plan,
        "preference_applied": preference is not None,
    }


def _select_context(
    calendar: dict[str, Any], gmail: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    for item in [*(calendar.get("active_contexts") or []), *(calendar.get("upcoming_contexts") or [])]:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode") or "")
        if mode in {"away", "hosting"}:
            candidates.append(
                {
                    "mode": mode,
                    "source": "Calendar",
                    "starts_at": str(item.get("starts_at") or ""),
                    "ends_at": str(item.get("ends_at") or ""),
                }
            )
    for item in gmail.get("notices") or []:
        if not isinstance(item, dict) or str(item.get("category") or "") != "travel":
            continue
        starts_at = str(item.get("starts_at") or "")
        if starts_at:
            candidates.append(
                {
                    "mode": "away",
                    "source": "Gmail",
                    "starts_at": starts_at,
                    "ends_at": str(item.get("ends_at") or ""),
                }
            )
    if candidates:
        return min(candidates, key=lambda item: item["starts_at"])
    # No event window is not evidence that the household is away. The normal
    # home plan is both useful and safer than inventing an absence period.
    return {
        "mode": "current",
        "source": "Home Assistant",
        "starts_at": "now",
        "ends_at": "",
    }


def _fallback_plan(
    mode: str,
    snapshot: dict[str, Any],
    temperature_setting: HouseholdPlanItem,
) -> HouseholdPlan:
    occupancy = str(snapshot.get("occupancy_status") or "unknown")
    if mode == "hosting":
        return HouseholdPlan(
            title="Hosting plan",
            summary="Prepare for a higher-activity household period while keeping comfort normal.",
            settings=[
                temperature_setting,
                HouseholdPlanItem(
                    label="Lights",
                    recommendation=(
                        "Keep normal comfort lighting available; do not apply "
                        "aggressive energy-saving schedules."
                    ),
                ),
                HouseholdPlanItem(
                    label="Garage door",
                    recommendation=(
                        "Normal open and close activity is expected during the gathering."
                    ),
                ),
                HouseholdPlanItem(
                    label="Appliances",
                    recommendation=(
                        "Higher short-term appliance and kitchen energy use can be expected."
                    ),
                ),
            ],
            expected_patterns=[
                "More people, motion, and lighting activity than the normal baseline.",
                "Dimmer-light activity can be used as a temporary room-activity signal.",
                "Repeated garage-door activity and a higher electricity draw may be normal.",
                f"Current occupancy is {occupancy}; use the event window when interpreting changes.",
            ],
            watch_for=[
                "Safety-critical smoke, leak, or equipment alerts.",
                "An open garage door after the hosting window ends.",
            ],
        )
    if mode == "current":
        return HouseholdPlan(
            title="Current at-home plan",
            summary="These are the household's preferred normal-home settings while someone is present.",
            settings=[
                HouseholdPlanItem(
                    label="Comfort temperature",
                    recommendation="Maintain the household's normal occupied comfort setpoint.",
                ),
                HouseholdPlanItem(
                    label="Lights",
                    recommendation="Keep lighting aligned with normal household use and room activity.",
                ),
                HouseholdPlanItem(
                    label="Room activity",
                    recommendation=(
                        "Use motion-enabled dimmer lights as a temporary room-activity signal "
                        "while they are on; their assumed automatic-off delay is 10 minutes."
                    ),
                ),
                HouseholdPlanItem(
                    label="Garage door",
                    recommendation="Normal household garage-door activity is expected.",
                ),
            ],
            expected_patterns=[
                "Normal motion, lighting, and appliance activity while the home is occupied.",
                "A motion-enabled dimmer that is on indicates current inferred activity in that room.",
                f"Current occupancy is {occupancy}; compare changes against the normal at-home pattern.",
            ],
            watch_for=[
                "Safety-critical smoke, leak, or equipment alerts.",
                "Activity that conflicts with the household's normal at-home preferences.",
            ],
        )
    return HouseholdPlan(
        title="Away-from-home plan",
        summary=_away_plan_summary(temperature_setting),
        settings=[
            temperature_setting,
            HouseholdPlanItem(
                label="Lights",
                recommendation="Turn off non-essential lights; retain any planned security lighting.",
            ),
            HouseholdPlanItem(
                label="Garage door",
                recommendation="Keep the garage door closed for the trip window.",
            ),
            HouseholdPlanItem(
                label="Appliances",
                recommendation="Washer, dryer, and other high-load appliances should be idle.",
            ),
        ],
        expected_patterns=[
            "Low occupancy, limited motion, and lower discretionary energy use.",
            "No washer or dryer cycles should occur during the trip window.",
            f"Current occupancy is {occupancy}; compare later activity against the away pattern.",
        ],
        watch_for=[
            "Unexpected interior motion or a garage door opening.",
            "A motion-enabled dimmer indicating room activity during the away window.",
            "Washer, dryer, or other high-load appliance activity.",
            "An unexpected sustained rise in household power use.",
        ],
    )


def _temperature_guidance(weather: dict[str, Any], mode: str) -> HouseholdPlanItem:
    current = weather.get("current") if isinstance(weather.get("current"), dict) else {}
    outdoor = current.get("temperature")
    unit = str(current.get("temperature_unit") or "°F")
    if mode == "hosting":
        return HouseholdPlanItem(
            label="Comfort temperature",
            recommendation="Keep the normal occupied comfort setpoint during the event.",
        )
    if mode == "current":
        return HouseholdPlanItem(
            label="Comfort temperature",
            recommendation="Maintain the household's normal occupied comfort setpoint.",
        )
    if isinstance(outdoor, (int, float)) and unit == "°F":
        if outdoor <= 62:
            return HouseholdPlanItem(
                label="Away heating",
                recommendation=(
                    "Set the heating target to about 62°F during the trip. This saves "
                    "heating energy while helping protect pipes in cold weather."
                ),
            )
        if outdoor >= 63:
            return HouseholdPlanItem(
                label="Away cooling",
                recommendation=(
                    "Set the cooling target to about 82°F during the trip. This reduces "
                    "air-conditioning energy compared with an occupied comfort setting."
                ),
            )
    return HouseholdPlanItem(
        label="Away temperature",
        recommendation=(
            "Use the household's configured away temperature setpoint. EcoNest could not "
            "determine whether heating or cooling guidance applies from the live weather."
        ),
    )


def _with_temperature_guidance(
    plan: HouseholdPlan,
    mode: str,
    temperature_setting: HouseholdPlanItem,
) -> HouseholdPlan:
    """Replace a model-selected temperature with bounded weather-based guidance."""
    non_temperature_settings = [
        item
        for item in plan.settings
        if "temperature" not in item.label.lower()
        and "thermostat" not in item.label.lower()
    ]
    updates: dict[str, Any] = {
        "settings": [temperature_setting, *non_temperature_settings]
    }
    if mode == "away":
        updates["summary"] = _away_plan_summary(temperature_setting)
    return plan.model_copy(update=updates)


def _with_plan_actions(
    plan: HouseholdPlan,
    snapshot: dict[str, Any],
    mode: str,
    temperature_setting: HouseholdPlanItem,
    preference: dict[str, Any] | None,
) -> HouseholdPlan:
    """Attach editable, confirmation-only actions from a saved plan or live state."""
    preferred_actions = _preferred_actions(preference)
    actions = preferred_actions or _default_actions(snapshot, mode, temperature_setting)
    return plan.model_copy(update={"actions": actions})


def _preferred_actions(
    preference: dict[str, Any] | None,
) -> list[HouseholdPlanAction]:
    """Use valid resident-saved actions as a reusable plan template."""
    if not preference:
        return []
    try:
        return HouseholdPlan.model_validate(preference).actions
    except Exception:
        return []


def _default_actions(
    snapshot: dict[str, Any], mode: str, temperature_setting: HouseholdPlanItem
) -> list[HouseholdPlanAction]:
    """Propose conservative actions from current state; never execute them here."""
    if mode != "away":
        return []
    actions: list[HouseholdPlanAction] = []
    inferred_active_light_ids = {
        str(item.get("entity_id"))
        for item in snapshot.get("inferred_room_activity", [])
        if isinstance(item, dict) and isinstance(item.get("entity_id"), str)
    }
    for light in snapshot.get("lights_on", [])[:6]:
        if isinstance(light, dict) and isinstance(light.get("entity_id"), str):
            if light["entity_id"] in inferred_active_light_ids:
                continue
            actions.append(HouseholdPlanAction(
                entity_id=light["entity_id"],
                entity_name=str(light.get("name") or light["entity_id"]),
                action="turn_off",
            ))
    for cover in snapshot.get("covers_open", [])[:3]:
        if not isinstance(cover, dict) or not isinstance(cover.get("entity_id"), str):
            continue
        text = f"{cover.get('entity_id', '')} {cover.get('name', '')}".lower()
        if "garage" in text:
            actions.append(HouseholdPlanAction(
                entity_id=cover["entity_id"],
                entity_name=str(cover.get("name") or cover["entity_id"]),
                action="close",
            ))
    target = 82.0 if temperature_setting.label == "Away cooling" else 62.0
    if temperature_setting.label in {"Away cooling", "Away heating"}:
        for climate in snapshot.get("temperature_readings", [])[:6]:
            if isinstance(climate, dict) and isinstance(climate.get("entity_id"), str):
                actions.append(HouseholdPlanAction(
                    entity_id=climate["entity_id"],
                    entity_name=str(climate.get("name") or climate["entity_id"]),
                    action="set_temperature",
                    temperature=target,
                ))
    return actions[:12]


def _away_plan_summary(temperature_setting: HouseholdPlanItem) -> str:
    """Describe an away target without misrepresenting it as occupied comfort."""
    if temperature_setting.label == "Away cooling":
        return (
            "During this away period, EcoNest recommends an energy-saving cooling "
            "target rather than the normal occupied comfort setting. Automated watering "
            "schedules can continue as programmed."
        )
    if temperature_setting.label == "Away heating":
        return (
            "During this away period, EcoNest recommends an energy-saving heating "
            "target that still helps protect the home in cold weather. Automated watering "
            "schedules can continue as programmed."
        )
    return (
        "During this away period, EcoNest recommends the household's configured away "
        "temperature setting and reduced discretionary energy use."
    )


def _plan_prompt(
    selected: dict[str, str],
    weather: dict[str, Any],
    snapshot: dict[str, Any],
    preference: dict[str, Any] | None = None,
) -> str:
    compact_snapshot = {
        "occupancy_status": snapshot.get("occupancy_status"),
        "lights_on": _names(snapshot.get("lights_on")),
        "switches_on": _names(snapshot.get("switches_on")),
        "covers_open": _names(snapshot.get("covers_open")),
        "active_motion": _names(snapshot.get("active_motion")),
        "inferred_room_activity": _names(snapshot.get("inferred_room_activity")),
        "inferred_activity_timeout_minutes": snapshot.get("inferred_activity_timeout_minutes"),
        "top_power_now_w": snapshot.get("top_power_now_w", [])[:4],
    }
    preference_guidance = ""
    if preference:
        preference_guidance = (
            "\n\nResident feedback from a previous plan in this same event category: "
            f"{json.dumps(preference)}\nUse it as a preference for how the resident wants "
            "plans written and prioritized. Re-check current weather and live home facts; do "
            "not copy old device-state claims or override bounded temperature guidance."
        )
    return (
        "You are EcoNest's household planning assistant. Create a concise, resident-facing "
        "plan for the supplied event window. Suggest settings and expected patterns only; do "
        "not claim that any setting has been changed. For an away period, include temperature, "
        "lights, garage door, appliance-idle expectations, and unexpected activity to watch. "
        "For hosting, include comfortable temperature, normal lights, expected garage activity, "
        "and expected higher activity. Motion-enabled dimmer lights are temporary inferred room-activity "
        "signals, not verified presence sensors. For current at-home context, describe the household's "
        "preferred normal occupied settings and expected normal activity. Never invent devices or dates.\n\n"
        f"Context window: {json.dumps(selected)}\n"
        f"Weather: {json.dumps(weather)}\n"
        f"Current Home Assistant snapshot: {json.dumps(compact_snapshot)}"
        f"{preference_guidance}"
    )


def _names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item.get("name") or item.get("entity_id"))
        for item in value
        if isinstance(item, dict)
    ][:6]


def _matches_selected_mode(plan: HouseholdPlan, mode: str) -> bool:
    """Reject a plan whose wording contradicts the verified context window."""
    text = " ".join(
        [
            plan.title,
            plan.summary,
            *(item.label for item in plan.settings),
            *(item.recommendation for item in plan.settings),
            *plan.expected_patterns,
            *plan.watch_for,
        ]
    ).lower()
    away_terms = ("away", "trip", "travel", "absence", "unoccupied")
    if mode == "current":
        return not any(term in text for term in away_terms)
    if mode == "hosting":
        return not any(term in text for term in away_terms)
    return True


def _is_recommendation_only(plan: HouseholdPlan) -> bool:
    """Reject a plan that claims a requested setting is already in effect."""
    text = " ".join(
        [
            plan.title,
            plan.summary,
            *(item.label for item in plan.settings),
            *(item.recommendation for item in plan.settings),
            *plan.expected_patterns,
            *plan.watch_for,
        ]
    ).lower()
    unsupported_claims = (
        "will be set",
        "will be switched",
        "is closed",
        "are in idle",
        "will continue operation",
        "automated watering will",
    )
    return not any(claim in text for claim in unsupported_claims)
