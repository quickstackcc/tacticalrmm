import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_get_returns_json_on_200(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(200, json={"items": [1, 2]}))
        client = TrmmClient.from_env()
        result = await client.get("/agents/")
        assert result == {"items": [1, 2]}


@pytest.mark.asyncio
async def test_get_sends_api_key_header(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/").mock(return_value=httpx.Response(200, json={}))
        client = TrmmClient.from_env()
        await client.get("/agents/")
        sent = route.calls.last.request
        assert sent.headers["x-api-key"] == "test-token"


@pytest.mark.asyncio
async def test_get_raises_auth_error_on_401(trmm_env):
    from trmm_mcp.exceptions import TrmmAuthError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(401, json={"detail": "no"}))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmAuthError) as exc_info:
            await client.get("/agents/")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_raises_not_found_on_404(trmm_env):
    from trmm_mcp.exceptions import TrmmNotFoundError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/missing/").mock(return_value=httpx.Response(404))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmNotFoundError):
            await client.get("/agents/missing/")


@pytest.mark.asyncio
async def test_get_raises_generic_api_error_on_500(trmm_env):
    from trmm_mcp.exceptions import TrmmApiError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(500, text="kaboom"))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmApiError) as exc_info:
            await client.get("/agents/")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_get_passes_query_params(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/").mock(return_value=httpx.Response(200, json={}))
        client = TrmmClient.from_env()
        await client.get("/agents/", params={"online": "true", "client": "5"})
        sent = route.calls.last.request
        assert sent.url.params["online"] == "true"
        assert sent.url.params["client"] == "5"


@pytest.mark.asyncio
async def test_get_wraps_network_error_as_trmm_api_error(trmm_env):
    from trmm_mcp.exceptions import TrmmApiError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(side_effect=httpx.ConnectError("dns fail"))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmApiError) as exc_info:
            await client.get("/agents/")
        assert exc_info.value.status_code == 0
        msg = str(exc_info.value).lower()
        assert "network error" in msg or "dns fail" in msg


@pytest.mark.asyncio
async def test_get_wraps_invalid_json_as_trmm_api_error(trmm_env):
    from trmm_mcp.exceptions import TrmmApiError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(200, text="<html>oops</html>"))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmApiError) as exc_info:
            await client.get("/agents/")
        assert exc_info.value.status_code == 200


@pytest.mark.asyncio
async def test_post_sends_json_body(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/scripts/run/").mock(return_value=httpx.Response(200, json={"id": 1}))
        client = TrmmClient.from_env()
        result = await client.post("/scripts/run/", json={"agent": "a", "script_id": 7})
        assert result == {"id": 1}
        assert route.calls.last.request.content == b'{"agent":"a","script_id":7}'


@pytest.mark.asyncio
async def test_post_returns_none_on_empty_body(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/x/reboot/").mock(return_value=httpx.Response(204))
        client = TrmmClient.from_env()
        result = await client.post("/agents/x/reboot/")
        assert result is None


@pytest.mark.asyncio
async def test_patch_sends_partial_update(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/42/").mock(
            return_value=httpx.Response(200, json={"acked": True})
        )
        client = TrmmClient.from_env()
        result = await client.patch("/alerts/42/", json={"acked": True})
        assert result == {"acked": True}
        assert route.calls.last.request.method == "PATCH"
