from typing import Any

from ..trmm_client import TrmmClient


async def kill_process(
    *,
    client: TrmmClient,
    agent_id: str,
    pid: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    if pid is None and not name:
        raise ValueError("kill_process requires either pid or name")
    body: dict[str, Any] = {}
    if pid is not None:
        body["pid"] = pid
    if name is not None:
        body["name"] = name
    return await client.post(f"/agents/{agent_id}/processes/kill/", json=body)


async def restart_service(
    *, client: TrmmClient, agent_id: str, service_name: str
) -> dict[str, Any]:
    return await client.post(
        f"/agents/{agent_id}/services/restart/",
        json={"service_name": service_name},
    )


async def reboot_agent(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/reboot/")
