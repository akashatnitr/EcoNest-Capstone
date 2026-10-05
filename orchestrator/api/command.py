"""Browser command-console routes and model-assisted command interpretation."""

import re
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.agents.history_analysis import (
    HistoryAnalysisPlan,
    RoomComfortQueryPlan,
)
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.api.auth import UserProfile, get_optional_current_user
from orchestrator.config import get_settings
from orchestrator.core.command_feedback import (
    read_pending_corrections,
    review_command_correction,
    save_command_feedback,
)
from orchestrator.core.database import get_mysql_session
from orchestrator.core.execution_trace import (
    reset_execution_trace_context,
    set_execution_trace_context,
    task_execution_trace,
)
from orchestrator.core.permissions import AGENT_RUN, USER_ADMIN, Role, has_permission
from orchestrator.llm.client import LLMClient
from orchestrator.llm.models import LLMMessage
from orchestrator.mcp.executor import MCPToolExecutor

router = APIRouter(tags=["command"])
settings = get_settings()
_command_orchestrator = AgentOrchestrator()

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
_CONDITIONAL_IRRIGATION_WORDS = ("sprinkler", "irrigation", "watering")
_CYCLE_HISTORY_WORDS = ("when", "last", "recent", "how long", "did")
_DEVICE_ACTION_PHRASES = (
    "turn on",
    "turn off",
    "set ",
    "open ",
    "close ",
    "dim ",
    "brighten ",
)
_ADVISORY_TERMS = {
    "energy_recommendation": (
        "energy",
        "electricity",
        "electric bill",
        "power usage",
        "power use",
        "high power",
        "unusual power",
        "unusual energy",
        "efficiency",
        "save energy",
        "reduce consumption",
        "reduce my bill",
        "cost saving",
    ),
    "security_recommendation": (
        "security",
        "secure",
        "safety recommendation",
        "security assessment",
        "suspicious activity",
    ),
    "watering_recommendation": (
        "watering recommendation",
        "irrigation recommendation",
        "sprinkler recommendation",
        "watering advice",
        "irrigation advice",
        "should i water",
        "do i need to water",
        "is watering needed",
        "should i run the sprinkler",
    ),
    "event_history": (
        "recent important events",
        "important events in my home",
        "recent home events",
        "recent household events",
        "what happened in my home",
    ),
}


class InterpretCommandRequest(BaseModel):
    """Natural-language instruction submitted from the Command Center."""

    intent: str = Field(min_length=1, max_length=1_000)


class ConditionSpec(BaseModel):
    """One inventory-backed predicate that must hold before device control."""

    entity_id: str
    property: str = "state"
    operator: Literal["equals", "not_equals", "greater_than", "less_than"]
    value: str | float | int | bool


class CommandInterpretation(BaseModel):
    """Strict structured result returned by the local decision model."""

    request_kind: Literal[
        "device_control",
        "energy_recommendation",
        "security_recommendation",
        "watering_recommendation",
        "irrigation_question",
        "event_history",
        "historical_analysis",
        "home_state_query",
    ] = "device_control"
    entity_id: str | None = None
    action: (
        Literal[
            "turn_on",
            "turn_off",
            "set_brightness",
            "set_temperature",
            "open",
            "close",
        ]
        | None
    ) = None
    brightness: int | None = Field(default=None, ge=0, le=100)
    temperature: float | None = None
    conditions: list[ConditionSpec] = Field(default_factory=list, max_length=4)
    confidence: float = Field(ge=0.0, le=1.0)
    clarification: str | None = None
    history_analysis: HistoryAnalysisPlan | None = None
    room_comfort_query: RoomComfortQueryPlan | None = None


class InterpretCommandResponse(BaseModel):
    """UI-safe result for a device proposal or an advisory review request."""

    status: Literal[
        "confirmation_required",
        "advisory_started",
        "answer_started",
        "condition_not_met",
        "needs_clarification",
    ]
    request_kind: (
        Literal[
            "device_control",
            "energy_recommendation",
            "security_recommendation",
            "watering_recommendation",
            "irrigation_question",
            "event_history",
            "historical_analysis",
            "home_state_query",
        ]
        | None
    ) = None
    task_id: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    action: str | None = None
    brightness: int | None = None
    temperature: float | None = None
    confidence: float | None = None
    conditions: list[ConditionSpec] = Field(default_factory=list)
    history_analysis: HistoryAnalysisPlan | None = None
    room_comfort_query: RoomComfortQueryPlan | None = None
    execution_trace: list[dict[str, object]] = Field(default_factory=list)
    message: str


class SubmitCommandTaskRequest(BaseModel):
    """A confirmed Command Center task awaiting agent execution."""

    intent: str = Field(min_length=1, max_length=1_000)
    payload: dict[str, Any]
    timeout_seconds: int = Field(default=30, ge=1, le=180)


class CommandTaskResponse(BaseModel):
    """Task acknowledgement returned to the Command Center."""

    task_id: str
    status: str


class CommandFeedbackRequest(BaseModel):
    """A resident's rating of one completed Command Center result."""

    task_id: str = Field(min_length=1, max_length=100)
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1_000)
    correction: str | None = Field(default=None, max_length=2_000)


class CorrectionReviewRequest(BaseModel):
    """A human decision supported by a written check of home evidence."""

    task_id: str = Field(min_length=1, max_length=100)
    user_id: int
    approved: bool
    evidence_note: str = Field(min_length=10, max_length=2_000)


@router.get("/command", response_class=HTMLResponse)
async def command_page() -> HTMLResponse:
    """Serve the authenticated natural-language command console."""
    page = Path(__file__).resolve().parents[1] / "static" / "command.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/command/access")
async def command_access() -> dict[str, bool]:
    """Expose whether this Command Center instance requires a sign-in."""
    return {"authentication_required": settings.COMMAND_CENTER_AUTH_REQUIRED}


@router.post("/command/interpret", response_model=InterpretCommandResponse)
async def interpret_command(
    request: InterpretCommandRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
) -> InterpretCommandResponse:
    """Interpret a command without controlling a device.

    The model can select only from the live MCP device inventory. Its answer is
    shown to the user for confirmation; this route never creates a task or
    calls a Home Assistant service.
    """
    actor = _command_actor(current_user)
    task_id = f"command-interpretation-{uuid4()}"
    direct_request_kind = _direct_advisory_request_kind(request.intent)
    if direct_request_kind == "irrigation_question" or (
        direct_request_kind is not None and _history_plan_hint(request.intent) is None
    ):
        return await _start_advisory_request(
            request.intent,
            direct_request_kind,
            actor,
        )
    action_safety_message = _action_safety_clarification(request.intent)
    if action_safety_message is not None:
        return InterpretCommandResponse(
            status="needs_clarification",
            message=action_safety_message,
        )
    try:
        inventory = await MCPToolExecutor().read_resource(
            "home://devices",
            user_id=str(actor.id),
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
    try:
        condition_catalog = await MCPToolExecutor().read_resource(
            "home://conditions",
            user_id=str(actor.id),
            task_id=task_id,
            agent="command_interpreter",
            source="command_center",
        )
    except Exception:
        condition_catalog = {"conditions": []}
    catalog_conditions = condition_catalog.get("conditions", [])
    if not isinstance(catalog_conditions, list):
        catalog_conditions = []
    model_devices = _relevant_devices(request.intent, compact_devices)
    trace_context = set_execution_trace_context(task_id, "command_interpreter")
    try:
        interpretation = await _interpret_with_model(
            request.intent,
            model_devices,
            _relevant_condition_capabilities(request.intent, catalog_conditions),
        )
    finally:
        reset_execution_trace_context(trace_context)
    interpretation_trace = task_execution_trace(task_id)
    interpretation = _complete_unambiguous_plan(
        request.intent,
        interpretation,
        compact_devices,
        _relevant_condition_capabilities(request.intent, catalog_conditions),
    )
    interpretation = _validate_history_analysis_intent(request.intent, interpretation)
    interpretation = _validate_room_comfort_intent(request.intent, interpretation)
    response = _validated_interpretation(interpretation, compact_devices).model_copy(
        update={"execution_trace": interpretation_trace}
    )
    if response.status == "confirmation_required":
        response = await _evaluate_conditional_action(
            request.intent,
            response,
            actor.role,
            task_id,
            catalog_conditions,
        )
    if response.status != "advisory_started":
        if response.status != "answer_started":
            return response

    return await _start_advisory_request(
        request.intent,
        response.request_kind,
        actor,
        response,
    )


async def _start_advisory_request(
    intent: str,
    request_kind: Literal[
        "energy_recommendation",
        "security_recommendation",
        "watering_recommendation",
        "irrigation_question",
        "event_history",
        "historical_analysis",
        "home_state_query",
    ]
    | None,
    actor: UserProfile,
    response: InterpretCommandResponse | None = None,
) -> InterpretCommandResponse:
    """Submit a non-controlling advisory request after safe routing."""
    if not has_permission(actor.role, AGENT_RUN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent:run permission required",
        )
    if request_kind is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if response is None:
        response = _validated_interpretation(
            CommandInterpretation(request_kind=request_kind, confidence=1.0), []
        )
    agent_type = {
        "energy_recommendation": "energy",
        "security_recommendation": "security",
        "watering_recommendation": "irrigation",
        "irrigation_question": "irrigation_question",
        "event_history": "event_history",
        "historical_analysis": "event_history",
        "home_state_query": "home_data",
    }[request_kind]
    payload: dict[str, Any] = {
        "type": agent_type,
        "recommendation_only": True,
    }
    if request_kind not in {
        "irrigation_question",
        "event_history",
        "historical_analysis",
        "home_state_query",
    }:
        payload["use_llm"] = True
    if request_kind == "event_history":
        cycle_search = _appliance_cycle_search(intent)
        if cycle_search:
            payload.update(
                {"history_kind": "appliance_cycle", "event_search": cycle_search}
            )
    if (
        request_kind == "historical_analysis"
        and response is not None
        and response.history_analysis
    ):
        payload["history_analysis"] = response.history_analysis.model_dump()
    if (
        request_kind == "home_state_query"
        and response is not None
        and response.room_comfort_query
    ):
        payload["room_comfort_query"] = response.room_comfort_query.model_dump()
    task_id = await _command_orchestrator.submit_http_api(
        intent=intent,
        payload=payload,
        user_id=str(actor.id),
        user_role=actor.role,
        timeout_seconds=180,
    )
    return response.model_copy(update={"task_id": task_id})


@router.post(
    "/command/task",
    response_model=CommandTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_command_task(
    request: SubmitCommandTaskRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
) -> CommandTaskResponse:
    """Submit a confirmed Command Center task without exposing the MCP API."""
    actor = _command_actor(current_user)
    if not has_permission(actor.role, AGENT_RUN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent:run permission required",
        )
    task_id = await _command_orchestrator.submit_http_api(
        intent=request.intent,
        payload=request.payload,
        user_id=str(actor.id),
        user_role=actor.role,
        timeout_seconds=request.timeout_seconds,
    )
    return CommandTaskResponse(task_id=task_id, status="submitted")


@router.get("/command/task/{task_id}")
async def command_task_status(
    task_id: str,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
) -> dict[str, Any]:
    """Return the result of a Command Center task after access is checked."""
    _command_actor(current_user)
    result = await _command_orchestrator.get_result(task_id)
    if result is None:
        return {
            "task_id": task_id,
            "status": "running",
            "result": None,
            "execution_trace": task_execution_trace(task_id),
        }
    return {
        "task_id": task_id,
        "status": "completed" if result.success else "failed",
        "result": result.data,
        "message": result.message,
        "agent": result.agent,
        "confidence": result.confidence,
        "metadata": result.metadata,
        "execution_trace": task_execution_trace(task_id),
    }


@router.post("/command/feedback")
async def submit_command_feedback(
    request: CommandFeedbackRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Store a rating for a completed task owned by the current resident."""
    actor = _command_actor(current_user)
    context = _command_orchestrator.get_task_context(request.task_id)
    result = await _command_orchestrator.get_result(request.task_id)
    if context is None or result is None or context[1] != str(actor.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Completed result not found for this user",
        )
    return await save_command_feedback(
        session,
        task_id=request.task_id,
        user_id=actor.id,
        prompt=context[0],
        response_text=_feedback_response_text(result.data, result.message),
        agent=result.agent,
        result_status="completed" if result.success else "failed",
        rating=request.rating,
        comment=(request.comment.strip() or None)
        if request.comment is not None
        else None,
        correction=(request.correction.strip() or None)
        if request.correction is not None
        else None,
    )


def _feedback_reviewer(current_user: UserProfile | None) -> UserProfile:
    """Require an authenticated operator; demo access cannot approve training data."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to review corrections",
        )
    if not has_permission(current_user.role, USER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user:admin permission required",
        )
    return current_user


@router.get("/command/feedback/pending")
async def pending_command_corrections(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> list[dict[str, Any]]:
    """List resident corrections awaiting a grounded human review."""
    _feedback_reviewer(current_user)
    return await read_pending_corrections(session)


@router.post("/command/feedback/review")
async def review_feedback_correction(
    request: CorrectionReviewRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Approve or reject one correction after the reviewer checks source evidence."""
    reviewer = _feedback_reviewer(current_user)
    found = await review_command_correction(
        session,
        task_id=request.task_id,
        user_id=request.user_id,
        reviewer_id=reviewer.id,
        approved=request.approved,
        evidence_note=request.evidence_note.strip(),
    )
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Correction not found"
        )
    return {
        "task_id": request.task_id,
        "review_status": "approved" if request.approved else "rejected",
    }


def _feedback_response_text(data: Any, message: str) -> str:
    """Keep a short, reviewable copy of the answer the user evaluated."""
    if isinstance(data, dict):
        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer[:4_000]
        recommendations = data.get("recommendations")
        if isinstance(recommendations, list):
            parts = [
                f"{item.get('action', 'Recommendation')}: {item.get('reasoning', '')}"
                if isinstance(item, dict)
                else item
                for item in recommendations
                if isinstance(item, dict | str)
            ]
            if parts:
                return " ".join(parts)[:4_000]
    return message[:4_000]


def _command_actor(current_user: UserProfile | None) -> UserProfile:
    """Return the signed-in user or the explicitly enabled demo operator."""
    if current_user is not None:
        return current_user
    if settings.COMMAND_CENTER_AUTH_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return UserProfile(
        id=0,
        email="demo-operator@local",
        role=Role.HOMEOWNER.value,
        household_id=None,
        is_active=True,
    )


def _direct_advisory_request_kind(
    intent: str,
) -> (
    Literal[
        "energy_recommendation",
        "security_recommendation",
        "watering_recommendation",
        "irrigation_question",
    ]
    | None
):
    """Route clear advisory requests before costly device-plan generation.

    Advisory categories are non-controlling and therefore safe to recognize from
    their request language. A concrete device-control phrase always takes
    precedence and continues to require inventory-backed model planning.
    """
    lowered = f" {intent.lower().strip()} "
    if any(phrase in lowered for phrase in _DEVICE_ACTION_PHRASES):
        return None
    if any(term in lowered for term in _ADVISORY_TERMS["energy_recommendation"]):
        return "energy_recommendation"
    if any(term in lowered for term in _ADVISORY_TERMS["security_recommendation"]):
        return "security_recommendation"
    if any(term in lowered for term in _ADVISORY_TERMS["watering_recommendation"]):
        return "watering_recommendation"
    irrigation_activity_words = (*_CONDITIONAL_IRRIGATION_WORDS, "watered")
    factual_words = (
        "was ",
        "is ",
        "did ",
        "when ",
        "how long",
        "run today",
        "has ",
        "recent",
        "last ",
    )
    if (
        any(word in lowered for word in irrigation_activity_words)
        and any(word in lowered for word in factual_words)
        and not any(word in lowered for word in ("recommend", "advice", "should"))
    ):
        return "irrigation_question"
    if any(term in lowered for term in _CONDITIONAL_IRRIGATION_WORDS):
        if any(word in lowered for word in factual_words) and not any(
            word in lowered for word in ("recommend", "advice", "should")
        ):
            return "irrigation_question"
        return "watering_recommendation"
    return None


def _appliance_cycle_search(intent: str) -> str | None:
    """Extract a simple appliance name from factual completed-cycle questions."""
    lowered = " ".join(intent.lower().split())
    if "cycle" not in lowered or not any(
        word in lowered for word in _CYCLE_HISTORY_WORDS
    ):
        return None
    match = re.search(
        r"(?:when (?:was|did)|last|recent) (?:the )?(?:last )?"
        r"(?P<appliance>[a-z0-9][a-z0-9 _-]{0,60}?) cycle\b",
        lowered,
    )
    if match is None:
        return None
    appliance = " ".join(match.group("appliance").split())
    return appliance if appliance not in {"", "the"} else None


def _validate_history_analysis_intent(
    intent: str, interpretation: CommandInterpretation
) -> CommandInterpretation:
    """Correct an unsafe advisory misroute when wording requests a factual history metric.

    Gemma remains the primary planner. This narrow validator prevents a broad
    recommendation label from swallowing questions that explicitly request a
    measurable past fact, such as an average duration or event count.
    """
    plan = _history_plan_hint(intent)
    if plan is None:
        return interpretation
    if (
        interpretation.request_kind == "historical_analysis"
        and interpretation.history_analysis is not None
    ):
        model_plan = interpretation.history_analysis
        return interpretation.model_copy(
            update={
                "history_analysis": model_plan.model_copy(
                    update={
                        "operation": plan.operation,
                        "subject": model_plan.subject or plan.subject,
                        "period_days": model_plan.period_days or plan.period_days,
                    }
                )
            }
        )
    # A clearly expressed metric is a deterministic part of the resident's
    # request. Preserve Gemma's valid bounded-plan refinements, but correct an
    # in-category mistake such as interpreting “How many ...?” as a latest
    # event lookup.
    return interpretation.model_copy(
        update={
            "request_kind": "historical_analysis",
            "history_analysis": plan,
            "entity_id": None,
            "action": None,
            "conditions": [],
            "clarification": None,
        }
    )


def _history_plan_hint(intent: str) -> HistoryAnalysisPlan | None:
    """Recognize factual appliance-history questions before advisory routing."""
    lowered = " ".join(intent.lower().split())
    if any(phrase in lowered for phrase in _DEVICE_ACTION_PHRASES):
        return None
    if any(word in lowered for word in _CONDITIONAL_IRRIGATION_WORDS):
        return None
    subject = _history_appliance_subject(lowered)
    if subject is None:
        return None
    latest_words = ("latest", "most recent", "last", "recent")
    metric_words = (
        "average",
        "avg",
        "how many",
        "count",
        "total",
        "how long",
        "usually",
        "typical",
        *latest_words,
    )
    period_days = _history_period_days(lowered)
    if not any(word in lowered for word in metric_words) or (
        period_days is None and not any(word in lowered for word in latest_words)
    ):
        return None
    operation = (
        "average_duration"
        if any(word in lowered for word in ("average", "avg", "usually", "typical"))
        else "count"
        if "how many" in lowered or "count" in lowered
        else "total_duration"
        if "total" in lowered or "how long" in lowered
        else "latest"
        if any(word in lowered for word in latest_words)
        else "list"
    )
    return HistoryAnalysisPlan(
        scope="appliance_cycles",
        operation=operation,
        subject=subject,
        period_days=period_days,
    )


def _history_appliance_subject(intent: str) -> str | None:
    """Extract an appliance name from common cycle, load, and runtime phrasing."""
    patterns = (
        r"\b(?:run[\s-]?time|running time|cycle (?:length|duration|time))\s+"
        r"(?:of|for)\s+(?:my|the)\s+(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)"
        r"(?=\s+(?:in|over|during|within|for|past|last)\b|[?.!,;]|$)",
        r"\b(?:show|list)\s+(?:my|the)\s+(?:most recent|latest|last|recent)\s+"
        r"(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+cycles?\b",
        r"\b(?:most recent|latest|last|recent)(?:\s+completed)?\s+"
        r"(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+cycles?\b",
        r"\b(?:how many|number of|count of)\s+(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+"
        r"(?:completed\s+)?cycles?\b",
        r"\bmy\s+(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+(?:cycles?|loads?)\b",
        r"\bthe\s+(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+(?:cycles?|loads?)\b",
        r"\bmy\s+(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+"
        r"(?:has\s+)?run\b",
        r"\bthe\s+(?P<subject>[a-z0-9][a-z0-9 _-]{0,60}?)\s+"
        r"(?:has\s+)?run\b",
    )
    for pattern in patterns:
        match = re.search(pattern, intent)
        if match is not None:
            subject = " ".join(match.group("subject").split())
            if subject not in {"", "the", "my"}:
                return subject
    return None


def _history_period_days(intent: str) -> int | None:
    """Convert common requested history windows into an explicit bounded duration."""
    match = re.search(
        r"\b(?:past|last|over)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(day|week|month|year)s?\b",
        intent,
    )
    if match is None:
        named_periods = (
            ("this month", 30),
            ("this week", 7),
            ("past year", 365),
            ("last year", 365),
            ("this year", 365),
            ("today", 1),
        )
        return next((days for phrase, days in named_periods if phrase in intent), None)
    numbers = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    amount_text, unit = match.groups()
    amount = int(amount_text) if amount_text.isdigit() else numbers[amount_text]
    multiplier = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
    return min(amount * multiplier, 3_650)


def _validate_room_comfort_intent(
    intent: str, interpretation: CommandInterpretation
) -> CommandInterpretation:
    """Make explicit room-state wording override an incorrect model metric."""
    if interpretation.request_kind == "historical_analysis":
        return interpretation
    plan = _room_comfort_plan_hint(intent)
    if plan is None:
        return interpretation
    return interpretation.model_copy(
        update={
            "request_kind": "home_state_query",
            "room_comfort_query": plan,
            "entity_id": None,
            "action": None,
            "conditions": [],
            "clarification": None,
        }
    )


def _room_comfort_plan_hint(intent: str) -> RoomComfortQueryPlan | None:
    """Recognize factual room-condition grammar without naming rooms or devices."""
    lowered = " ".join(intent.lower().split())
    if any(phrase in lowered for phrase in _DEVICE_ACTION_PHRASES):
        return None
    if not any(phrase in lowered for phrase in ("what", "current", "tell me")):
        return None
    metric = (
        "target_temperature"
        if "comfort" in lowered
        or "target temperature" in lowered
        or "setpoint" in lowered
        else "current_temperature"
        if "temperature" in lowered
        else "humidity"
        if "humidity" in lowered
        else "hvac_mode"
        if "hvac" in lowered or "mode" in lowered
        else None
    )
    if metric is None:
        return None
    room_match = re.search(
        r"\b(?:in|for)\s+(?:the\s+)?([a-z0-9][a-z0-9 _-]{0,60}?)(?:\?|$)",
        lowered,
    ) or re.search(
        r"\b(?:is|are)\s+(?:the\s+)?([a-z0-9][a-z0-9 _-]{0,60}?)\s+"
        r"(?:using|set to|at)\b",
        lowered,
    )
    if room_match is None:
        return None
    room = " ".join(room_match.group(1).split())
    return RoomComfortQueryPlan(room=room, metric=metric)


async def _interpret_with_model(
    intent: str,
    devices: list[dict[str, Any]],
    condition_catalog: list[dict[str, Any]],
) -> CommandInterpretation:
    """Ask the local model to map language to one listed device and action."""
    prompt = (
        "Interpret this smart-home request. First choose request_kind: "
        "energy_recommendation when the user asks about energy use, efficiency, "
        "power, cost, or savings; security_recommendation when they ask for a "
        "security assessment, safety advice, or suspicious activity review; "
        "watering_recommendation when they ask for advice or a recommendation about watering, "
        "irrigation, or sprinklers; irrigation_question when they ask a factual question about "
        "whether irrigation or a sprinkler ran, is on, or when it ran; event_history when they ask "
        "about recent important household events; historical_analysis when they ask a factual question "
        "about retained home history that requires a calculation, comparison, count, trend, or a specific "
        "past appliance cycle; and "
        "home_state_query when they ask for a factual current room condition such as a comfort target, "
        "temperature, humidity, or HVAC mode; and "
        "device_control only when they request a concrete device action. Energy, "
        "Energy, security, and watering recommendation requests are advisory: return their request_kind "
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
        "does not control a device. For a device action with an if, when, or unless "
        "condition, return every condition in conditions using only entity_id and property "
        "from the condition catalog. Operators are equals, not_equals, greater_than, or "
        "less_than. Never omit a condition or invent a catalog entry.\n\n"
        "For historical_analysis, return history_analysis with: scope appliance_cycles for appliance "
        "cycle questions or important_events otherwise; operation one of latest, average_duration, count, "
        "total_duration, or list; the plain-language subject/device name if one is named; and period_days "
        "when a time range is requested. This only chooses a read-only data plan; never invent facts.\n\n"
        "For home_state_query, return room_comfort_query with the named room and one metric: "
        "target_temperature for comfort temperature/setpoint, current_temperature for the measured temperature, "
        "humidity, or hvac_mode. This is read-only; never turn a factual question into an energy recommendation.\n\n"
        f"User command: {intent}\n\n"
        f"Device inventory: {devices}\n\n"
        f"Condition catalog: {condition_catalog}"
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


def _relevant_condition_capabilities(
    intent: str, conditions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retrieve a compact live condition catalog for the planner prompt.

    Home Assistant may expose hundreds of entities. The evaluator still receives
    the full catalog, but Gemma should reason only over a small room/device
    relevant slice so its structured response remains reliable.
    """
    query_terms = _meaningful_terms(intent)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for condition in conditions:
        searchable = " ".join(
            str(condition.get(key) or "") for key in ("entity_id", "name", "domain")
        )
        overlap = len(query_terms & _meaningful_terms(searchable))
        if overlap:
            ranked.append((overlap, condition))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("entity_id") or "")))
    return [condition for _, condition in ranked[:16]]


def _complete_unambiguous_plan(
    intent: str,
    interpretation: CommandInterpretation,
    devices: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
) -> CommandInterpretation:
    """Fill a narrowly obvious plan when the small local model omits fields.

    This is not a second action planner: it only uses an inventory match when
    the prompt names one supported action and one clearly matching domain.
    """
    lowered = intent.lower()
    action = interpretation.action
    if "turn on" in lowered:
        action = "turn_on"
    elif "turn off" in lowered:
        action = "turn_off"
    if action is None:
        return interpretation

    devices_by_entity = {
        str(device.get("entity_id")): device
        for device in devices
        if device.get("entity_id")
    }
    available_entities = set(devices_by_entity)
    preferred_domains = [
        domain
        for word, domain in (("light", "light"), ("switch", "switch"), ("fan", "fan"))
        if word in lowered
    ]
    target_terms = _action_target_terms(lowered)
    entity_id = interpretation.entity_id
    if entity_id not in available_entities:
        entity_id = None
    elif (
        preferred_domains
        and devices_by_entity[entity_id].get("domain") not in preferred_domains
    ):
        entity_id = None
    elif not _action_target_matches_device(target_terms, devices_by_entity[entity_id]):
        entity_id = None
    if entity_id is None:
        candidates = [
            device
            for device in devices
            if (not preferred_domains or device.get("domain") in preferred_domains)
            and _action_target_matches_device(target_terms, device)
        ]
        ranked = sorted(
            (
                (
                    len(
                        _meaningful_terms(intent)
                        & _meaningful_terms(
                            f"{device.get('name') or ''} {device.get('entity_id') or ''}"
                        )
                    ),
                    device,
                )
                for device in candidates
            ),
            key=lambda item: (-item[0], str(item[1].get("entity_id") or "")),
        )
        if ranked and ranked[0][0] > 0:
            top_score, top_device = ranked[0]
            next_score = ranked[1][0] if len(ranked) > 1 else -1
            if top_score > next_score:
                entity_id = str(top_device.get("entity_id") or "") or None

    # A condition is meaningful only when the resident actually supplied one.
    # Gemma sees a condition catalog for supported conditional commands and can
    # occasionally attach a live state as an invented precondition to an
    # ordinary action (for example, dimming a light).  Never evaluate or carry
    # forward a condition that is not expressed in the request itself.
    has_explicit_condition = bool(re.search(r"\b(if|when|unless)\b", lowered))
    inferred_conditions = interpretation.conditions if has_explicit_condition else []
    explicit_setpoint = "temperature target" in lowered or "thermostat" in lowered
    if has_explicit_condition and explicit_setpoint:
        value_match = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(?:degrees?|°)?\s*(?:f|fahrenheit)?\b", lowered
        )
        climate = next(
            (item for item in conditions if item.get("domain") == "climate"), None
        )
        if value_match and climate is not None:
            properties = set(climate.get("properties") or [])
            property_name = (
                "temperature"
                if "temperature" in properties
                else "current_temperature"
                if "current_temperature" in properties
                else ""
            )
            if property_name:
                inferred_conditions = [
                    ConditionSpec(
                        entity_id=str(climate.get("entity_id")),
                        property=property_name,
                        operator="equals",
                        value=float(value_match.group(1)),
                    )
                ]

    return interpretation.model_copy(
        update={
            "entity_id": entity_id,
            "action": action,
            "conditions": inferred_conditions,
            "clarification": None
            if entity_id and action
            else interpretation.clarification,
        }
    )


def _action_safety_clarification(intent: str) -> str | None:
    """Reject broad or multi-device control before an inventory/model plan."""
    lowered = " ".join(intent.lower().split())
    if "unlock" in lowered or re.search(
        r"\b(?:every|all)\b.*\b(?:light|door)", lowered
    ):
        return (
            "EcoNest cannot prepare a combined whole-home or door-unlock command. "
            "Please request one specific device action at a time."
        )
    if lowered in {
        "make it warmer.",
        "make it warmer",
        "make it cooler.",
        "make it cooler",
    }:
        return "Please include the room and target temperature."
    if re.fullmatch(r"(?:please )?turn (?:on|off) (?:the )?lights?\.?", lowered):
        return "Please include the room or the specific light to control."
    if re.fullmatch(
        r"(?:please )?turn (?:on|off) (?:the )?(dryer|washer|tv|television|xbox)\.?",
        lowered,
    ):
        return (
            "Please identify the specific appliance control. EcoNest will not assume that "
            "a feature switch represents the whole appliance."
        )
    return None


def _action_target_terms(intent: str) -> set[str]:
    """Return the room/device words that must match a proposed control target."""
    ignored = {
        "action",
        "brightness",
        "brighten",
        "cooler",
        "degree",
        "dim",
        "f",
        "fahrenheit",
        "light",
        "on",
        "off",
        "percent",
        "set",
        "temperature",
        "thermostat",
        "warmer",
    }
    return _meaningful_terms(intent) - ignored


def _action_target_matches_device(
    target_terms: set[str], device: dict[str, Any]
) -> bool:
    """Require a named room or device word before proposing a control target."""
    if not target_terms:
        return False
    searchable = f"{device.get('name') or ''} {device.get('entity_id') or ''}"
    return bool(target_terms & _meaningful_terms(searchable))


def _validated_interpretation(
    interpretation: CommandInterpretation,
    devices: list[dict[str, Any]],
) -> InterpretCommandResponse:
    """Reject model output that is not a safe, inventory-backed proposal."""
    if interpretation.request_kind == "energy_recommendation":
        return InterpretCommandResponse(
            status="advisory_started",
            request_kind="energy_recommendation",
            confidence=interpretation.confidence,
            message=(
                "EcoNest is preparing an energy recommendation based on the "
                "available home data."
            ),
        )
    if interpretation.request_kind == "security_recommendation":
        return InterpretCommandResponse(
            status="advisory_started",
            request_kind="security_recommendation",
            confidence=interpretation.confidence,
            message=("EcoNest is preparing a security assessment and recommendations."),
        )
    if interpretation.request_kind == "watering_recommendation":
        return InterpretCommandResponse(
            status="advisory_started",
            request_kind="watering_recommendation",
            confidence=interpretation.confidence,
            message="EcoNest is preparing a weather-aware watering recommendation.",
        )
    if interpretation.request_kind == "irrigation_question":
        return InterpretCommandResponse(
            status="answer_started",
            request_kind="irrigation_question",
            confidence=interpretation.confidence,
            message="EcoNest is checking retained irrigation activity.",
        )
    if interpretation.request_kind == "event_history":
        return InterpretCommandResponse(
            status="answer_started",
            request_kind="event_history",
            confidence=interpretation.confidence,
            message="EcoNest is checking retained important household events.",
        )
    if interpretation.request_kind == "historical_analysis":
        plan = interpretation.history_analysis
        if plan is None:
            return _clarification_response(interpretation)
        return InterpretCommandResponse(
            status="answer_started",
            request_kind="historical_analysis",
            confidence=interpretation.confidence,
            history_analysis=plan,
            message="Gemma selected a read-only historical-data analysis plan.",
        )
    if interpretation.request_kind == "home_state_query":
        plan = interpretation.room_comfort_query
        if plan is None:
            return _clarification_response(interpretation)
        return InterpretCommandResponse(
            status="answer_started",
            request_kind="home_state_query",
            confidence=interpretation.confidence,
            room_comfort_query=plan,
            message="Gemma selected a read-only current room-state query.",
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
        conditions=interpretation.conditions,
        confidence=interpretation.confidence,
        message=(
            f"I understood: {action.replace('_', ' ')}{value} → {target}. "
            "Is that correct?"
        ),
    )


async def _evaluate_conditional_action(
    intent: str,
    response: InterpretCommandResponse,
    role: str,
    task_id: str,
    condition_catalog: list[dict[str, Any]],
) -> InterpretCommandResponse:
    """Check a supported irrigation precondition before proposing device control.

    A natural-language condition must never be dropped from an otherwise valid
    device proposal. The first supported condition is a negative irrigation-use
    window, backed by retained completed irrigation runs.
    """
    if response.conditions:
        return await _evaluate_discovered_conditions(
            response, role, task_id, condition_catalog
        )

    lowered = intent.lower()
    has_condition = bool(re.search(r"\b(if|when|unless)\b", lowered))
    if not has_condition:
        return response
    if not any(word in lowered for word in _CONDITIONAL_IRRIGATION_WORDS):
        return _unsupported_condition_response()

    days_match = re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", lowered)
    is_negative = bool(re.search(r"\b(has not|hasn't|not|was not|wasn't)\b", lowered))
    is_today = "today" in lowered
    if days_match is not None and is_negative:
        days = min(max(int(days_match.group(1)), 1), 7)
        sql = (
            "SELECT COUNT(*) AS run_count, MAX(ended_at) AS last_run_at "
            "FROM irrigation_runs "
            f"WHERE started_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL {days} DAY)"
        )
        params: dict[str, Any] = {}
        expected_runs = 0
        condition_description = f"no recorded irrigation runs in the last {days} days"
    elif is_today and not is_negative:
        central = ZoneInfo("America/Chicago")
        now = datetime.now(central)
        start_local = datetime.combine(now.date(), time.min, tzinfo=central)
        end_local = datetime.combine(now.date(), time.max, tzinfo=central)
        sql = (
            "SELECT COUNT(*) AS run_count, MAX(ended_at) AS last_run_at "
            "FROM irrigation_runs "
            "WHERE started_at >= :start_at AND started_at <= :end_at"
        )
        params = {
            "start_at": start_local.astimezone(UTC).replace(tzinfo=None),
            "end_at": end_local.astimezone(UTC).replace(tzinfo=None),
        }
        expected_runs = 1
        condition_description = "a recorded irrigation run today in Central time"
    else:
        return _unsupported_condition_response()
    try:
        result = await MCPToolExecutor().execute(
            "query_mysql",
            {"sql": sql, "params": params},
            role=role,
            task_id=task_id,
            agent="command_condition_evaluator",
            source="command_center",
        )
        rows = (
            result.result if result.success and isinstance(result.result, list) else []
        )
        run_count = int(rows[0].get("run_count") or 0) if rows else 0
    except Exception:
        return InterpretCommandResponse(
            status="needs_clarification",
            message=(
                "EcoNest could not verify the irrigation condition, so it did not "
                "prepare a device action. Please try again after the data connection is restored."
            ),
        )

    condition_met = run_count == 0 if expected_runs == 0 else run_count >= expected_runs
    if not condition_met:
        return InterpretCommandResponse(
            status="condition_not_met",
            message=(
                f"EcoNest found {run_count} recorded irrigation run"
                f"{'s' if run_count != 1 else ''}; the condition requires "
                f"{condition_description}, so it did not prepare the requested light action."
            ),
        )
    return response.model_copy(
        update={
            "message": (f"EcoNest found {condition_description}. {response.message}")
        }
    )


def _unsupported_condition_response() -> InterpretCommandResponse:
    """Refuse a condition that EcoNest cannot evaluate without guessing."""
    return InterpretCommandResponse(
        status="needs_clarification",
        message=(
            "EcoNest recognized a conditional action, but cannot verify that condition "
            "yet. It did not prepare an unconditional device command."
        ),
    )


async def _evaluate_discovered_conditions(
    response: InterpretCommandResponse,
    role: str,
    task_id: str,
    condition_catalog: list[dict[str, Any]],
) -> InterpretCommandResponse:
    """Evaluate scalar conditions against dynamically discovered HA entities."""
    known_properties = {
        str(item.get("entity_id")): set(item.get("properties") or [])
        for item in condition_catalog
        if isinstance(item, dict)
    }
    evidence: list[str] = []
    for condition in response.conditions:
        properties = known_properties.get(condition.entity_id)
        if properties is None or condition.property not in properties:
            return _unsupported_condition_response()
        try:
            tool_result = await MCPToolExecutor().execute(
                "ha_get_state",
                {"entity_id": condition.entity_id},
                role=role,
                task_id=task_id,
                agent="command_condition_evaluator",
                source="command_center",
            )
        except Exception:
            return _unsupported_condition_response()
        state = tool_result.result if tool_result.success else {}
        if not isinstance(state, dict):
            return _unsupported_condition_response()
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        actual = (
            state.get("state")
            if condition.property == "state"
            else attributes.get(condition.property)
        )
        if actual is None or not _condition_matches(actual, condition):
            return InterpretCommandResponse(
                status="condition_not_met",
                message=(
                    f"EcoNest checked {condition.entity_id} {condition.property.replace('_', ' ')} "
                    "and the requested condition is not currently met, so it did not "
                    "prepare the device action."
                ),
            )
        evidence.append(f"{condition.entity_id} {condition.property.replace('_', ' ')}")
    return response.model_copy(
        update={
            "message": (f"EcoNest verified: {', '.join(evidence)}. {response.message}")
        }
    )


def _condition_matches(actual: Any, condition: ConditionSpec) -> bool:
    """Apply one limited comparison without coercing arbitrary objects."""
    if condition.operator in {"greater_than", "less_than"}:
        try:
            left, right = float(actual), float(condition.value)
        except (TypeError, ValueError):
            return False
        return left > right if condition.operator == "greater_than" else left < right
    try:
        equal = abs(float(actual) - float(condition.value)) < 0.01
        return equal if condition.operator == "equals" else not equal
    except (TypeError, ValueError):
        pass
    left, right = str(actual).lower(), str(condition.value).lower()
    return left == right if condition.operator == "equals" else left != right


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
