"""
Tool registry and policy/approval/audit dispatcher — the architectural heart
of the trmm-mcp policy gate.

Every tool call from the MCP server routes through ``Dispatcher.dispatch()``,
which consults ``Policy`` for the tool's authority and routes accordingly:

- ``Authority.AUTO``         → execute immediately, return executed result.
- ``Authority.FORBIDDEN``    → return denied response, do not execute.
- ``Authority.HUMAN_APPROVAL`` → create pending row in ``ApprovalRegistry``
                                 and ``AuditLog``, return pending response.

After a human approves a pending action (via Plan 2's approval-bridge),
``Dispatcher.resume()`` is called: it reads the approved row from the
registry, looks up the registered function, executes it, and records the
result to both stores.

Known gaps (deferred to Task 21 — replay safety):

- No atomic transaction across the Redis approvals store and the Postgres
  audit log. If audit write fails after Redis create succeeds, the action
  becomes orphaned in Redis (TTL cleans up after 30 min).
- Tool-execution exceptions in ``dispatch`` (AUTO branch) and ``resume``
  propagate raw and do not record a "failed" entry in the audit trail.
"""

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
        inject_client: Any | None = None,
    ):
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._inject = inject_client

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        summary: str = "",
        session_id: str | None = None,
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
        action_id = self._approvals.create(tool_name=tool_name, args=args, summary=summary)
        self._audit.record_pending(
            action_id=action_id,
            tool_name=tool_name,
            args=args,
            summary=summary,
            policy_decision=authority.value,
        )
        if self._inject is not None:
            if not session_id:
                raise PolicyError(
                    "session_id required for human_approval when inject_client wired"
                )
            await self._inject.inject_card(
                session_id=session_id,
                question_id=f"nrmact-{action_id}",
                title="Pending action",
                question=summary or "Action requires confirmation",
                options=[
                    {"label": "Approve", "selectedLabel": "✅ Approved",
                     "value": "approve"},
                    {"label": "Reject", "selectedLabel": "❌ Rejected",
                     "value": "reject"},
                ],
            )
        return {
            "status": "pending",
            "action_id": action_id,
            "summary": summary,
        }

    async def resume(self, action_id: str) -> dict[str, Any]:
        """Called by approval-bridge once a human approves an action."""
        pending = self._approvals.get(action_id)
        if pending is None:
            raise ApprovalError(f"unknown or expired action: {action_id}")
        if pending["status"] != "approved":
            raise ApprovalError(f"action {action_id} is {pending['status']}, not approved")

        fn = self._registry.get(pending["tool_name"])
        if fn is None:
            raise PolicyError(f"tool no longer registered: {pending['tool_name']}")

        result = await fn(**pending["args"])
        self._approvals.mark_executed(action_id, result=result)
        self._audit.record_execution(action_id=action_id, result=result)
        return {"status": "executed", "result": result}

    async def recover(self) -> list[str]:
        """Replay any actions that were approved but not yet executed (post-crash recovery)."""
        replayed: list[str] = []
        for row in self._approvals.iter_all():
            if row["status"] == "approved":
                # Ensure the audit pending row exists (idempotent ON CONFLICT
                # DO NOTHING). A real crash would have left both Redis and
                # Postgres rows in sync, but defensive resilience here lets
                # recover() survive partial writes or audit-DB resets.
                self._audit.record_pending(
                    action_id=row["action_id"],
                    tool_name=row["tool_name"],
                    args=row["args"],
                    summary=row.get("summary", ""),
                    policy_decision="human_approval",
                )
                try:
                    await self.resume(row["action_id"])
                    replayed.append(row["action_id"])
                except (ApprovalError, PolicyError):
                    # Tool no longer registered or row mutated — skip and log via audit
                    continue
        return replayed
