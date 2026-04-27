from typing import Any

from ..trmm_client import TrmmClient


async def list_agents(
    *,
    client: TrmmClient,
    online: bool | None = None,
    client_id: int | None = None,
    site_id: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if online is not None:
        params["online"] = "true" if online else "false"
    if client_id is not None:
        params["client"] = str(client_id)
    if site_id is not None:
        params["site"] = str(site_id)
    return await client.get("/agents/", params=params or None)


async def get_agent(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.get(f"/agents/{agent_id}/")
