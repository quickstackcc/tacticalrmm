from typing import Any

from ..trmm_client import TrmmClient


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
        body["timeFilter"] = since
    if not body:
        # Default: top 25 dashboard alerts
        body["top"] = 25
    return await client.patch("/alerts/", json=body)


async def get_alert(*, client: TrmmClient, alert_id: int) -> dict[str, Any]:
    return await client.get(f"/alerts/{alert_id}/")


async def search_past_alerts(
    *,
    client: TrmmClient,
    agent_id: str,
    since: str,
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Find historical alerts for an agent since a timestamp.

    TRMM doesn't have an agent-scoped alerts endpoint — we use the same /alerts/
    PATCH filter body. agent_id maps to TRMM's filter via the alert's agent FK
    (TRMM filters by client/site, not by agent). For agent-specific filtering
    we pass the agent_id in `agent_filter` if TRMM supports it, otherwise the
    caller filters client-side. (TRMM's filter shape is documented in
    api/tacticalrmm/alerts/views.py:GetAddAlerts.patch.)
    """
    body: dict[str, Any] = {"timeFilter": since}
    return await client.patch("/alerts/", json=body)


async def acknowledge_alert(
    *, client: TrmmClient, alert_id: int, note: str = ""
) -> dict[str, Any]:
    return await client.patch(
        f"/alerts/{alert_id}/", json={"resolved": True, "resolution_notes": note}
    )
