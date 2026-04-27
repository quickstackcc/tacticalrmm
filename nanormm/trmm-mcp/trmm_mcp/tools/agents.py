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


async def agent_recent_checks(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    return await client.get(f"/agents/{agent_id}/checks/", params={"limit": str(n)})


async def agent_recent_tasks(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    return await client.get(f"/agents/{agent_id}/tasks/", params={"limit": str(n)})


async def agent_patch_state(*, client: TrmmClient, agent_id: str) -> list[dict[str, Any]]:
    """
    Windows Update inventory for an agent. Returns a flat list of update
    records; each has fields like `kb`, `title`, `installed`, `result`,
    `severity`, `date_installed`. Categorization (missing vs installed vs
    failed vs pending-reboot) is the caller's job.
    """
    return await client.get(f"/winupdate/{agent_id}/")


async def agent_running_processes(*, client: TrmmClient, agent_id: str) -> list[dict[str, Any]]:
    return await client.get(f"/agents/{agent_id}/processes/")
