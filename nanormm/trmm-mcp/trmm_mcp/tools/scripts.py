from typing import Any

from ..trmm_client import TrmmClient


async def script_history(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    """
    Recent agent activity history. TRMM returns all activity types
    (script_run, cmd_run, agent_install, etc.). Filter by `type` field
    in caller if you only want script runs.
    """
    rows = await client.get(f"/agents/{agent_id}/history/")
    # TRMM doesn't paginate this endpoint; trim client-side.
    if isinstance(rows, list):
        return rows[:n]
    return rows


ALLOWED_SHELLS = {"cmd", "powershell", "bash"}


async def run_script_on_agent(
    *,
    client: TrmmClient,
    agent_id: str,
    script_id: int,
    args: list[str] | None = None,
) -> dict[str, Any]:
    """
    Invoke a TRMM-library script by ID.

    The agent **cannot** ship a script body — it can only invoke scripts that
    already exist in TRMM's library.  Adding new scripts is a tech-led workflow
    in the TRMM UI.
    """
    body: dict[str, Any] = {"script_id": script_id}
    if args:
        body["args"] = list(args)
    return await client.post(f"/agents/{agent_id}/scripts/run/", json=body)


async def run_inline_command(
    *,
    client: TrmmClient,
    agent_id: str,
    shell: str,
    command: str,
) -> dict[str, Any]:
    """
    Run an arbitrary shell command on an agent.

    Permanently `human_approval` in policy.yaml — never graduates to auto.
    """
    if shell not in ALLOWED_SHELLS:
        raise ValueError(
            f"shell must be one of {sorted(ALLOWED_SHELLS)}, got {shell!r}"
        )
    return await client.post(
        f"/agents/{agent_id}/cmd/",
        json={"shell": shell, "command": command},
    )
