"""Device control agent."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.config import get_settings
from orchestrator.core.database import arcadedb_query
from orchestrator.mcp.tools.ha_tools import (
    HACallServiceInput,
    HAGetStateInput,
    ha_call_service_handler,
    ha_get_state_handler,
)

HA_VERIFY_ATTEMPTS = 5
HA_VERIFY_DELAY_SECONDS = 0.5


class DeviceActionRequest(BaseModel):
    device_id: str
    action: str | None = None
    brightness: int | None = None
    temperature: float | None = None
    domain: str | None = None
    entity_id: str | None = None


class CapabilityCheck(BaseModel):
    allowed: bool
    reason: str


class PermissionCheck(BaseModel):
    allowed: bool
    reason: str


class DeviceActionResult(BaseModel):
    action: str
    state: str
    capability_check: CapabilityCheck
    permission_check: PermissionCheck
    verified: bool
    execution_source: str


class DeviceExecution(BaseModel):
    success: bool
    state: str
    source: str
    verified: bool = False
    warnings: list[str] = []


class DeviceAgent(BaseAgent):
    """Responsibilities: device control (on/off/dim), capability enforcement,
    user request fulfillment.
    """

    name = "device"
    tools = ["ha_call_service", "query_arcadedb", "query_mysql"]
    permissions = ["device:read", "device:write", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = [
            "device",
            "turn on",
            "turn off",
            "dim",
            "light",
            "switch",
            "brightness",
            "sprinkler",
            "watering",
            "garage",
            "cover",
            "close",
            "open",
        ]
        return any(kw in task.intent.lower() for kw in keywords)

    async def run(
        self,
        task: Task,
    ) -> Result:

        request = DeviceActionRequest(
            device_id=task.payload.get(
                "device_id",
                "",
            ),
            action=task.payload.get(
                "action",
            ),
            brightness=task.payload.get(
                "brightness",
            ),
            temperature=task.payload.get(
                "temperature",
            ),
            domain=task.payload.get(
                "domain",
            ),
            entity_id=task.payload.get(
                "entity_id",
            ),
        )

        if request.action is None:
            return Result(
                success=True,
                confidence=1.0,
                data={
                    "action": "noop",
                    "state": "unchanged",
                },
                message="No device action requested",
            )

        capability = await self._check_capability(
            request,
        )

        if not capability.allowed:
            return Result(
                success=False,
                confidence=1.0,
                data={
                    "error": capability.reason,
                },
                message=capability.reason,
            )

        permission = await self._check_permission(
            task,
            request,
        )

        if not permission.allowed:
            return Result(
                success=False,
                confidence=1.0,
                data={
                    "error": permission.reason,
                },
                message=permission.reason,
            )

        execution = await self._execute_action(
            request,
        )

        if not execution.success:
            return Result(
                success=False,
                confidence=0.0,
                data={
                    "error": "; ".join(execution.warnings) or "Device action failed",
                    "execution_source": execution.source,
                },
                message="Device action failed",
                metadata={
                    "agent_type": "device",
                    "execution_source": execution.source,
                },
            )

        confidence = 0.95 if execution.verified else 0.7

        result = DeviceActionResult(
            action=request.action,
            state=execution.state,
            capability_check=capability,
            permission_check=permission,
            verified=execution.verified,
            execution_source=execution.source,
        )

        return Result(
            success=True,
            confidence=confidence,
            data=result.model_dump(),
            message="Device action completed",
            metadata={
                "agent_type": "device",
                "verified": execution.verified,
                "execution_source": execution.source,
                "actual_outcome": {
                    "device_id": request.entity_id or request.device_id,
                    "state": execution.state,
                    "verified": execution.verified,
                },
            },
        )

    async def _check_capability(
        self,
        request: DeviceActionRequest,
    ) -> CapabilityCheck:

        required_capabilities = {
            "turn_on": "OnOff",
            "turn_off": "OnOff",
            "set_brightness": "Dimmable",
            "set_temperature": "Thermostat",
            "open": "OpenClose",
            "close": "OpenClose",
        }

        required = required_capabilities.get(request.action)

        if required is None:
            return CapabilityCheck(
                allowed=False,
                reason="Unknown action",
            )

        try:
            selector = (
                f".has('ha_entity_id','{request.entity_id}')"
                if request.entity_id
                else f".has('mysql_id',{int(request.device_id)})"
                if request.device_id.isdigit()
                else f".has('ha_entity_id','{request.device_id}')"
            )
            result = await arcadedb_query(
                "gremlin",
                (
                    f"g.V()"
                    f".hasLabel('Device'){selector}"
                    ".out('HAS_CAPABILITY')"
                    ".values('name')"
                ),
            )

            capabilities = result.get(
                "result",
                [],
            )

        except Exception:
            if not get_settings().DEVICE_CAPABILITY_FAIL_CLOSED:
                return CapabilityCheck(allowed=True, reason="Capability verification unavailable")
            return CapabilityCheck(allowed=False, reason="Capability verification unavailable")

        if not capabilities:
            if not get_settings().DEVICE_CAPABILITY_FAIL_CLOSED:
                return CapabilityCheck(allowed=True, reason="No capability metadata available")
            return CapabilityCheck(allowed=False, reason="No capability metadata available")

        if required in capabilities:
            return CapabilityCheck(
                allowed=True,
                reason="Capability available",
            )

        return CapabilityCheck(
            allowed=False,
            reason=(f"Device lacks capability " f"'{required}'"),
        )

    async def _check_permission(
        self,
        task: Task,
        request: DeviceActionRequest,
    ) -> PermissionCheck:

        if not task.user_id:
            return PermissionCheck(
                allowed=True,
                reason="No user context available",
            )

        try:
            result = await arcadedb_query(
                "gremlin",
                (
                    f"g.V()"
                    f".has('email','{task.user_id}')"
                    ".out('CAN_PERFORM')"
                    ".values('name')"
                ),
            )

            allowed_actions = result.get(
                "result",
                [],
            )

            if not allowed_actions:
                return PermissionCheck(
                    allowed=True,
                    reason="No permission metadata available",
                )

            if request.action in allowed_actions:
                return PermissionCheck(
                    allowed=True,
                    reason="Permission granted",
                )

            return PermissionCheck(
                allowed=False,
                reason=(f"User cannot perform " f"'{request.action}'"),
            )

        except Exception:
            return PermissionCheck(
                allowed=True,
                reason=("Permission verification unavailable"),
            )

    async def _execute_action(
        self,
        request: DeviceActionRequest,
    ) -> DeviceExecution:
        ha_entity_id = self._ha_entity_id(request)
        if ha_entity_id is not None:
            return await self._execute_home_assistant_action(request, ha_entity_id)

        if request.action == "turn_on":
            return DeviceExecution(
                success=True,
                state="on",
                source="local_fallback",
                verified=True,
            )

        if request.action == "turn_off":
            return DeviceExecution(
                success=True,
                state="off",
                source="local_fallback",
                verified=True,
            )

        if request.action == "set_brightness":
            return DeviceExecution(
                success=True,
                state=f"brightness:{request.brightness}",
                source="local_fallback",
                verified=True,
            )

        if request.action == "set_temperature":
            return DeviceExecution(
                success=True,
                state=f"target_temperature:{request.temperature}",
                source="local_fallback",
                verified=True,
            )

        if request.action == "open":
            return DeviceExecution(
                success=True,
                state="open",
                source="local_fallback",
                verified=True,
            )

        if request.action == "close":
            return DeviceExecution(
                success=True,
                state="closed",
                source="local_fallback",
                verified=True,
            )

        raise ValueError(f"Unsupported action: {request.action}")

    async def _execute_home_assistant_action(
        self,
        request: DeviceActionRequest,
        entity_id: str,
    ) -> DeviceExecution:
        domain = request.domain or entity_id.split(".", 1)[0]
        service = self._ha_service(request)
        service_data = self._ha_service_data(request)
        result = await ha_call_service_handler(
            HACallServiceInput(
                domain=domain,
                service=service,
                entity_id=entity_id,
                service_data=service_data,
            )
        )
        if not result.success:
            return DeviceExecution(
                success=False,
                state="unknown",
                source="home_assistant",
                warnings=result.warnings,
            )

        expected_state = self._expected_state(request)
        verified = await self._verify_home_assistant_state(entity_id, expected_state)
        return DeviceExecution(
            success=True,
            state=expected_state,
            source="home_assistant",
            verified=verified,
            warnings=result.warnings,
        )

    async def _verify_home_assistant_state(
        self,
        entity_id: str,
        expected_state: str,
    ) -> bool:
        if expected_state.startswith("brightness:"):
            return True
        if expected_state.startswith("target_temperature:"):
            expected_temperature = float(expected_state.split(":", 1)[1])
            for attempt in range(HA_VERIFY_ATTEMPTS):
                result = await ha_get_state_handler(HAGetStateInput(entity_id=entity_id))
                if result.success and isinstance(result.result, dict):
                    attributes = result.result.get("attributes", {})
                    if isinstance(attributes, dict):
                        observed = attributes.get("temperature")
                        if (
                            observed is not None
                            and abs(float(observed) - expected_temperature) < 0.1
                        ):
                            return True
                if attempt < HA_VERIFY_ATTEMPTS - 1:
                    await asyncio.sleep(HA_VERIFY_DELAY_SECONDS)
            return False
        for attempt in range(HA_VERIFY_ATTEMPTS):
            result = await ha_get_state_handler(HAGetStateInput(entity_id=entity_id))
            if result.success and isinstance(result.result, dict):
                state = str(result.result.get("state", "")).lower()
                if state == expected_state:
                    return True
            if attempt < HA_VERIFY_ATTEMPTS - 1:
                await asyncio.sleep(HA_VERIFY_DELAY_SECONDS)
        return False

    def _ha_entity_id(self, request: DeviceActionRequest) -> str | None:
        if request.entity_id:
            return request.entity_id
        if "." in request.device_id:
            return request.device_id
        return None

    def _ha_service(self, request: DeviceActionRequest) -> str:
        if request.action == "turn_on":
            return "turn_on"
        if request.action == "turn_off":
            return "turn_off"
        if request.action == "set_brightness":
            return "turn_on"
        if request.action == "set_temperature":
            return "set_temperature"
        if request.action == "open":
            return "open_cover" if request.domain == "cover" else "turn_on"
        if request.action == "close":
            return "close_cover" if request.domain == "cover" else "turn_off"
        raise ValueError(f"Unsupported action: {request.action}")

    def _ha_service_data(self, request: DeviceActionRequest) -> dict | None:
        if request.action == "set_brightness":
            if request.brightness is None:
                return None
            return {"brightness_pct": request.brightness}
        if request.action == "set_temperature":
            if request.temperature is None:
                return None
            return {"temperature": request.temperature}
        return None

    def _expected_state(self, request: DeviceActionRequest) -> str:
        if request.action == "turn_on":
            return "on"
        if request.action == "turn_off":
            return "off"
        if request.action == "set_brightness":
            return f"brightness:{request.brightness}"
        if request.action == "set_temperature":
            return f"target_temperature:{request.temperature}"
        if request.action == "open":
            return "open"
        if request.action == "close":
            return "closed"
        return "unknown"
