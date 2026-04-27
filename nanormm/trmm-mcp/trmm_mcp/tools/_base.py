from collections.abc import Awaitable, Callable
from typing import Any

from ..approvals import ApprovalRegistry
from ..audit import AuditLog
from ..exceptions import ApprovalError, PolicyError
from ..policy import Authority, Policy

ToolFn = Callable[..., Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}

    def register(self, *, name: str) -> Callable[[ToolFn], ToolFn]:
        def deco(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ValueError(f"duplicate tool: {name}")
            self._tools[name] = fn
            return fn

        return deco

    def get(self, name: str) -> ToolFn | None:
        return self._tools.get(name)

    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


class Dispatcher:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: Policy,
        approvals: ApprovalRegistry,
        audit: AuditLog,
    ):
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        summary: str = "",
    ) -> dict[str, Any]:
        fn = self._registry.get(tool_name)
        if fn is None:
            raise PolicyError(f"unknown tool: {tool_name}")

        authority = self._policy.authority(tool_name)

        if authority is Authority.FORBIDDEN:
            return {"status": "denied", "reason": "tool is forbidden by policy"}

        if authority is Authority.AUTO:
            result = await fn(**args)
            return {"status": "executed", "result": result}

        # human_approval
        action_id = self._approvals.create(
            tool_name=tool_name, args=args, summary=summary
        )
        self._audit.record_pending(
            action_id=action_id,
            tool_name=tool_name,
            args=args,
            policy_decision=authority.value,
        )
        return {"status": "pending", "action_id": action_id, "summary": summary}

    async def resume(self, action_id: str) -> dict[str, Any]:
        """Called by approval-bridge once a human approves an action."""
        pending = self._approvals.get(action_id)
        if pending is None:
            raise ApprovalError(f"unknown or expired action: {action_id}")
        if pending["status"] != "approved":
            raise ApprovalError(
                f"action {action_id} is {pending['status']}, not approved"
            )

        fn = self._registry.get(pending["tool_name"])
        if fn is None:
            raise PolicyError(f"tool no longer registered: {pending['tool_name']}")

        result = await fn(**pending["args"])
        self._approvals.mark_executed(action_id, result=result)
        self._audit.record_execution(action_id=action_id, result=result)
        return {"status": "executed", "result": result}
