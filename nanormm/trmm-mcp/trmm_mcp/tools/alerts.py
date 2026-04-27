from datetime import UTC, datetime
from typing import Any

from ..trmm_client import TrmmClient


def _iso_to_days_back(iso: str) -> int:
    """Convert an ISO 8601 timestamp to TRMM's `timeFilter` integer (days back from today).

    TRMM's `/alerts/` PATCH view does `int(request.data["timeFilter"])` and treats
    the value as days, computing `alert_time__gt today - timedelta(days=N)`. We
    keep the agent-facing API in ISO 8601 (more conventional) and translate here.
    """
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    delta = datetime.now(UTC) - parsed
    return max(1, delta.days)


async def list_alerts(
    *,
    client: TrmmClient,
    status: str | None = None,
    since: str | None = None,
    client_id: int | None = None,
    top: int | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    List TRMM alerts. TRMM's /alerts/ endpoint expects PATCH with a filter body.

    - top=N → returns {"alerts_count": int, "alerts": [...]} (top N unresolved+unsnoozed).
    - status='unresolved' → adds resolvedFilter=False.
    - status='snoozed' → adds snoozedFilter=True.
    - client_id → adds clientFilter=[client_id].
    - since=ISO timestamp → translated to TRMM's days-back integer.

    With no filters, returns the unresolved+unsnoozed top set (top defaults to 25).
    """
    body: dict[str, Any] = {}
    if top is not None:
        body["top"] = top
    if status == "unresolved":
        body["resolvedFilter"] = False
    elif status == "snoozed":
        body["snoozedFilter"] = True
    if client_id is not None:
        body["clientFilter"] = [client_id]
    if since is not None:
        body["timeFilter"] = _iso_to_days_back(since)
    if not body:
        # Default: top 25 dashboard alerts
        body["top"] = 25
    return await client.patch("/alerts/", json=body)


async def get_alert(*, client: TrmmClient, alert_id: int) -> dict[str, Any]:
    return await client.get(f"/alerts/{alert_id}/")


async def search_past_alerts(
    *,
    client: TrmmClient,
    agent_id: str,  # noqa: ARG001 - TRMM has no agent-scoped alerts filter; caller filters client-side
    since: str,
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Find historical alerts since an ISO timestamp.

    TRMM has no agent-scoped alerts filter — the agent_id parameter is accepted
    for API symmetry but ignored at the wire layer. The caller is responsible
    for filtering the returned list by agent.
    """
    return await client.patch("/alerts/", json={"timeFilter": _iso_to_days_back(since)})


async def acknowledge_alert(
    *, client: TrmmClient, alert_id: int, note: str = ""
) -> dict[str, Any]:
    return await client.patch(
        f"/alerts/{alert_id}/", json={"resolved": True, "resolution_notes": note}
    )
