"""Browser command-console routes and model-assisted command interpretation."""

from pathlib import Path
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.llm.client import LLMClient
from orchestrator.llm.models import LLMMessage
from orchestrator.mcp.executor import MCPToolExecutor

router = APIRouter(tags=["command"])

_SUPPORTED_ACTIONS = {
    "turn_on",
    "turn_off",
    "set_brightness",
    "set_temperature",
    "open",
    "close",
}
_QUERY_STOP_WORDS = {
    "a",
    "an",
    "at",
    "for",
    "in",
    "it",
    "my",
    "of",
    "on",
    "please",
    "switch",
    "the",
    "to",
    "turn",
}


class InterpretCommandRequest(BaseModel):
    """Natural-language instruction submitted from the Command Center."""

    intent: str = Field(min_length=1, max_length=1_000)


class CommandInterpretation(BaseModel):
    """Strict structured result returned by the local decision model."""

    request_kind: Literal[
        "device_control",
        "energy_recommendation",
        "security_recommendation",
    ] = "device_control"
    entity_id: str | None = None
    action: Literal[
        "turn_on",
        "turn_off",
        "set_brightness",
        "set_temperature",
        "open",
        "close",
    ] | None = None
    brightness: int | None = Field(default=None, ge=0, le=100)
    temperature: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    clarification: str | None = None


class InterpretCommandResponse(BaseModel):
    """UI-safe interpretation requiring confirmation before execution."""

    status: Literal["confirmation_required", "needs_clarification"]
    request_kind: Literal[
        "device_control",
        "energy_recommendation",
        "security_recommendation",
    ] | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    action: str | None = None
    brightness: int | None = None
    temperature: float | None = None
    confidence: float | None = None
    message: str


@router.get("/command", response_class=HTMLResponse)
async def command_page() -> HTMLResponse:
    """Serve the authenticated natural-language command console."""
    page = Path(__file__).resolve().parents[1] / "static" / "command.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.post("/command/interpret", response_model=InterpretCommandResponse)
async def interpret_command(
    request: InterpretCommandRequest,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> InterpretCommandResponse:
    """Interpret a command without controlling a device.

    The model can select only from the live MCP device inventory. Its answer is
    shown to the user for confirmation; this route never creates a task or
    calls a Home Assistant service.
    """
    task_id = f"command-interpretation-{uuid4()}"
    try:
        inventory = await MCPToolExecutor().read_resource(
            "home://devices",
            user_id=str(current_user.id),
            task_id=task_id,
            agent="command_interpreter",
            source="command_center",
        )
        devices = inventory.get("devices")
    except Exception:
        devices = []

    if not isinstance(devices, list):
        devices = []

    compact_devices = [
        {
            "entity_id": item.get("entity_id"),
            "name": item.get("name"),
            "domain": item.get("domain"),
            "state": item.get("state"),
            "actions": item.get("actions"),
        }
        for item in devices
        if isinstance(item, dict)
    ]
    model_devices = _relevant_devices(request.intent, compact_devices)
    interpretation = await _interpret_with_model(request.intent, model_devices)
    return _validated_interpretation(interpretation, compact_devices)


async def _interpret_with_model(
    intent: str,
    devices: list[dict[str, Any]],
) -> CommandInterpretation:
    """Ask the local model to map language to one listed device and action."""
    prompt = (
        "Interpret this smart-home request. First choose request_kind: "
        "energy_recommendation when the user asks about energy use, efficiency, "
        "power, cost, or savings; security_recommendation when they ask for a "
        "security assessment, safety advice, or suspicious activity review; and "
        "device_control only when they request a concrete device action. Energy "
        "and security recommendation requests are advisory: return their request_kind "
        "with null entity_id and action.\n\n"
        "For device_control, select an entity_id and action ONLY from the supplied "
        "device inventory. Do not invent an entity, action, or value. Never ask the "
        "user for an entity ID or other technical identifier. Treat singular and plural "
        "device wording as equivalent. When one device is the clear name-and-room "
        "match, select it even if the user's wording is not identical to its friendly "
        "name. If a device command is ambiguous, lacks a required value, or does not "
        "match one listed device, return null for entity_id and action and write a short "
        "clarification question. set_brightness requires brightness (0-100); "
        "set_temperature requires temperature. This only prepares a confirmation; it "
        "does not control a device.\n\n"
        f"User command: {intent}\n\n"
        f"Device inventory: {devices}"
    )
    try:
        return await LLMClient().generate_structured(
            [LLMMessage(role="user", content=prompt)],
            CommandInterpretation,
            temperature=0.0,
        )
    except Exception:
        return CommandInterpretation(
            confidence=0.0,
            clarification=(
                "EcoNest could not confidently interpret that command. "
                "Please rephrase it with the room and device name."
            ),
        )


def _relevant_devices(
    intent: str, devices: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Limit model context to inventory entries relevant to the user's wording.

    This is retrieval, not action selection: Gemma still chooses and returns the
    entity/action. It prevents a request such as "media room lights" being
    buried among unrelated switches and dozens of individually-addressable
    decorative-light segments.
    """
    query_terms = _meaningful_terms(intent)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for device in devices:
        searchable = " ".join(
            str(device.get(key) or "") for key in ("name", "entity_id", "domain")
        )
        overlap = len(query_terms & _meaningful_terms(searchable))
        if overlap:
            ranked.append((overlap, device))

    if not ranked:
        return devices[:20]
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("entity_id") or "")))
    return [device for _, device in ranked[:12]]


def _meaningful_terms(value: str) -> set[str]:
    """Normalize names so light and lights, for example, match each other."""
    terms = set(re.findall(r"[a-z0-9]+", value.lower()))
    normalized = {
        term[:-1] if term.endswith("s") and len(term) > 3 else term for term in terms
    }
    return normalized - _QUERY_STOP_WORDS


def _validated_interpretation(
    interpretation: CommandInterpretation,
    devices: list[dict[str, Any]],
) -> InterpretCommandResponse:
    """Reject model output that is not a safe, inventory-backed proposal."""
    if interpretation.request_kind == "energy_recommendation":
        return InterpretCommandResponse(
            status="confirmation_required",
            request_kind="energy_recommendation",
            confidence=interpretation.confidence,
            message=(
                "I understood: request an energy recommendation based on the "
                "available EcoNest data. Is that correct?"
            ),
        )
    if interpretation.request_kind == "security_recommendation":
        return InterpretCommandResponse(
            status="confirmation_required",
            request_kind="security_recommendation",
            confidence=interpretation.confidence,
            message=(
                "I understood: request a security assessment and recommendations. "
                "Is that correct?"
            ),
        )

    by_entity = {
        str(device.get("entity_id")): device
        for device in devices
        if device.get("entity_id")
    }
    device = by_entity.get(interpretation.entity_id or "")
    action = interpretation.action
    if device is None or action not in _SUPPORTED_ACTIONS:
        return _clarification_response(interpretation)

    supported_actions = device.get("actions")
    if not isinstance(supported_actions, list) or action not in supported_actions:
        return _clarification_response(interpretation)
    if action == "set_brightness" and interpretation.brightness is None:
        return _clarification_response(interpretation)
    if action == "set_temperature" and interpretation.temperature is None:
        return _clarification_response(interpretation)

    name = str(device.get("name") or interpretation.entity_id)
    target = f"{name} ({interpretation.entity_id})"
    value = ""
    if action == "set_brightness":
        value = f" to {interpretation.brightness}%"
    elif action == "set_temperature":
        value = f" to {interpretation.temperature:g}°"
    return InterpretCommandResponse(
        status="confirmation_required",
        request_kind="device_control",
        entity_id=interpretation.entity_id,
        entity_name=name,
        action=action,
        brightness=interpretation.brightness,
        temperature=interpretation.temperature,
        confidence=interpretation.confidence,
        message=(
            f"I understood: {action.replace('_', ' ')}{value} → {target}. "
            "Is that correct?"
        ),
    )


def _clarification_response(
    interpretation: CommandInterpretation,
) -> InterpretCommandResponse:
    """Return a model clarification without exposing invalid proposed targets."""
    return InterpretCommandResponse(
        status="needs_clarification",
        confidence=interpretation.confidence,
        message=(
            interpretation.clarification
            or "I could not match that command to one available device. "
            "Please include the room and device name."
        ),
    )
