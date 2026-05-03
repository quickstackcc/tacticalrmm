from typing import Any
from urllib.parse import quote

from ..trmm_client import TrmmClient


async def kill_process(
    *,
    client: TrmmClient,
    agent_id: str,
    pid: int,
) -> dict[str, Any]:
    return await client.delete(f"/agents/{agent_id}/processes/{int(pid)}/")


async def restart_service(
    *, client: TrmmClient, agent_id: str, service_name: str
) -> dict[str, Any]:
    svcname = quote(service_name, safe="")
    return await client.post(
        f"/services/{agent_id}/{svcname}/",
        json={"sv_action": "restart"},
    )


async def reboot_agent(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/reboot/")
