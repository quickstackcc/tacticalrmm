from typing import Any

from ..trmm_client import TrmmClient


async def script_history(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    return await client.get(
        f"/agents/{agent_id}/scripthistory/", params={"limit": str(n)}
    )
