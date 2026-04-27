from typing import Any

from ..trmm_client import TrmmClient


async def list_alerts(
    *,
    client: TrmmClient,
    status: str | None = None,
    since: str | None = None,
    client_id: int | None = None,
) -> list[dict[str, Any]]:
    """List recent TRMM alerts. status: 'all' | 'unresolved' | 'snoozed'."""
    params: dict[str, Any] = {}
    if status == "unresolved":
        params["resolved"] = "false"
    elif status == "snoozed":
        params["snoozed"] = "true"
    if since is not None:
        params["since"] = since
    if client_id is not None:
        params["client"] = str(client_id)
    return await client.get("/alerts/", params=params or None)


async def get_alert(*, client: TrmmClient, alert_id: int) -> dict[str, Any]:
    return await client.get(f"/alerts/{alert_id}/")


async def search_past_alerts(
    *,
    client: TrmmClient,
    agent_id: str,
    since: str,
) -> list[dict[str, Any]]:
    return await client.get("/alerts/", params={"agent": agent_id, "since": since})


async def acknowledge_alert(
    *, client: TrmmClient, alert_id: int, note: str = ""
) -> dict[str, Any]:
    return await client.patch(
        f"/alerts/{alert_id}/", json={"resolved": True, "resolution_notes": note}
    )
