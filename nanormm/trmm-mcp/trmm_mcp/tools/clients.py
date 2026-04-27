from typing import Any

from ..trmm_client import TrmmClient


async def query_clients(*, client: TrmmClient) -> list[dict[str, Any]]:
    """Return the full client → site tree."""
    return await client.get("/clients/")
