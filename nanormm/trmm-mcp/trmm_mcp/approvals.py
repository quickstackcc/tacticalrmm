import json
import secrets
from datetime import UTC, datetime
from typing import Any

import redis

from .exceptions import ApprovalError

_KEY_PREFIX = "nanormm:pending:"

# Status state machine: pending -> approved | rejected | expired
#                      approved -> executed
# Once executed, the row becomes terminal (idempotent).
_TERMINAL = {"executed", "rejected", "expired"}


class ApprovalRegistry:
    def __init__(self, client: redis.Redis, *, ttl_seconds: int):
        self._r = client
        self._ttl = ttl_seconds

    def create(self, *, tool_name: str, args: dict[str, Any], summary: str) -> str:
        action_id = f"act_{secrets.token_urlsafe(12)}"
        payload = {
            "action_id": action_id,
            "tool_name": tool_name,
            "args": args,
            "summary": summary,
            "status": "pending",
            "created_at": _now_iso(),
        }
        try:
            serialized = json.dumps(payload)
        except (TypeError, ValueError) as e:
            raise ApprovalError(
                f"cannot serialize action payload (non-JSON args?): {e}"
            ) from e
        self._r.set(_key(action_id), serialized, ex=self._ttl)
        return action_id

    def get(self, action_id: str) -> dict[str, Any] | None:
        raw = self._r.get(_key(action_id))
        if raw is None:
            return None
        return json.loads(raw)

    def mark_approved(self, action_id: str, *, approved_by: str) -> None:
        self._mutate(
            action_id,
            require_status={"pending"},
            update={"status": "approved", "approved_by": approved_by, "approved_at": _now_iso()},
        )

    def mark_rejected(self, action_id: str, *, rejected_by: str, reason: str = "") -> None:
        self._mutate(
            action_id,
            require_status={"pending"},
            update={
                "status": "rejected",
                "rejected_by": rejected_by,
                "reject_reason": reason,
                "rejected_at": _now_iso(),
            },
        )

    def mark_executed(self, action_id: str, *, result: Any) -> None:
        self._mutate(
            action_id,
            require_status={"approved"},
            update={"status": "executed", "result": result, "executed_at": _now_iso()},
        )

    def iter_all(self) -> list[dict[str, Any]]:
        """Return every action in the registry — used by Dispatcher.recover()."""
        out: list[dict[str, Any]] = []
        for k in self._r.scan_iter(match=f"{_KEY_PREFIX}*", count=200):
            raw = self._r.get(k)
            if raw is not None:
                out.append(json.loads(raw))
        return out

    def _mutate(
        self,
        action_id: str,
        *,
        require_status: set[str],
        update: dict[str, Any],
    ) -> None:
        key = _key(action_id)

        # Closure used by redis.transaction(): receives a pipeline that has
        # been WATCH'd on `key`. We read inside the WATCH window, validate,
        # then enter MULTI and queue the write. If anyone else mutates the
        # key between WATCH and EXEC, EXEC aborts and the helper retries.
        def txn(pipe: redis.client.Pipeline) -> None:
            raw = pipe.get(key)
            if raw is None:
                raise ApprovalError(f"unknown or expired action_id: {action_id}")
            current = json.loads(raw)
            if current["status"] in _TERMINAL:
                # Idempotent: terminal state, no-op.  Cancel the transaction
                # by aborting the pipeline so EXEC does nothing.
                pipe.unwatch()
                return
            if current["status"] not in require_status:
                pipe.unwatch()
                raise ApprovalError(
                    f"cannot transition from {current['status']!r} via {set(update.keys())}"
                )
            current.update(update)
            ttl = pipe.ttl(key)
            ex = max(ttl, 1) if ttl > 0 else self._ttl
            pipe.multi()
            pipe.set(key, json.dumps(current), ex=ex)

        try:
            self._r.transaction(txn, key, value_from_callable=False)
        except redis.WatchError:
            # Lost the race; let the caller decide whether to retry.  In
            # practice a competing approve/reject is the only realistic
            # source, and the second caller will hit a non-pending status
            # on retry which raises ApprovalError — desired behavior.
            raise ApprovalError(
                f"concurrent modification detected on action {action_id}; retry"
            ) from None


def _key(action_id: str) -> str:
    return _KEY_PREFIX + action_id


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
