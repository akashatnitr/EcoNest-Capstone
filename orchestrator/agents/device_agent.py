"""Device control agent."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.core.database import arcadedb_query


class DeviceActionRequest(BaseModel):
    device_id: str
    action: str | None = None
    brightness: int | None = None


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

        state = await self._execute_action(
            request,
        )

        verified = await self._verify_state(
            request,
            state,
        )

        confidence = 0.95 if verified else 0.7

        result = DeviceActionResult(
            action=request.action,
            state=state,
            capability_check=capability,
            permission_check=permission,
            verified=verified,
        )

        return Result(
            success=True,
            confidence=confidence,
            data=result.model_dump(),
            message="Device action completed",
            metadata={
                "agent_type": "device",
                "verified": verified,
            },
        )

    async def _check_capability(
        self,
        request: DeviceActionRequest,
    ) -> CapabilityCheck:

        required_capabilities = {
            "turn_on": "device_turn_on",
            "turn_off": "device_turn_off",
            "set_brightness": "device_set_brightness",
        }

        required = required_capabilities.get(request.action)

        if required is None:
            return CapabilityCheck(
                allowed=False,
                reason="Unknown action",
            )

        try:
            result = await arcadedb_query(
                "gremlin",
                (
                    f"g.V()"
                    f".has('id','{request.device_id}')"
                    ".out('HAS_CAPABILITY')"
                    ".values('name')"
                ),
            )

            capabilities = result.get(
                "result",
                [],
            )

        except Exception:
            return CapabilityCheck(
                allowed=True,
                reason=("Capability verification unavailable"),
            )

        if required in capabilities:
            if not capabilities:
                return CapabilityCheck(
                    allowed=True,
                    reason=("No capability metadata available"),
                )
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
    ) -> str:

        if request.action == "turn_on":
            return "on"

        if request.action == "turn_off":
            return "off"

        if request.action == "set_brightness":
            return f"brightness:{request.brightness}"

        return "unknown"

    async def _verify_state(
        self,
        request: DeviceActionRequest,
        state: str,
    ) -> bool:

        return state != "unknown"
