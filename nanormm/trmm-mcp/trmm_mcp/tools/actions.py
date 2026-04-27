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


ALLOWED_ARTIFACT_SETS = {
    "event_logs",
    "process_list",
    "network_connections",
    "scheduled_tasks",
    "installed_software",
}


async def collect_artifacts(
    *, client: TrmmClient, agent_id: str, artifact_set: str
) -> dict[str, Any]:
    if artifact_set not in ALLOWED_ARTIFACT_SETS:
        raise ValueError(
            f"unknown artifact_set {artifact_set!r}; must be one of {sorted(ALLOWED_ARTIFACT_SETS)}"
        )
    return await client.post(
        f"/agents/{agent_id}/artifacts/collect/",
        json={"artifact_set": artifact_set},
    )


async def isolate_host(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/isolate/")


async def unisolate_host(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/unisolate/")


async def disable_account(*, client: TrmmClient, agent_id: str, username: str) -> dict[str, Any]:
    return await client.post(
        f"/agents/{agent_id}/accounts/disable/",
        json={"username": username},
    )


async def pause_scheduled_task(
    *, client: TrmmClient, agent_id: str, task_id: int
) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/tasks/{task_id}/pause/")
