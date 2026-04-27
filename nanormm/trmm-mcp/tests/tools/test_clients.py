import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_query_clients_returns_tree(trmm_env):
    from trmm_mcp.tools.clients import query_clients
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"id": 1, "name": "Acme", "sites": [{"id": 10, "name": "HQ"}]},
        {"id": 2, "name": "Widgets Co", "sites": []},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/clients/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await query_clients(client=client)

    assert result[0]["name"] == "Acme"
    assert result[0]["sites"][0]["name"] == "HQ"
