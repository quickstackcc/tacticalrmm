import json as _json
from typing import Any

import httpx

from .exceptions import TrmmApiError, TrmmAuthError, TrmmNotFoundError
from .settings import Settings


class TrmmClient:
    # TODO(Task 18): TrmmClient should be a long-lived singleton owned by the
    # MCP server's lifespan, not constructed per-call via from_env(). Each
    # `from_env()` call leaks an httpx.AsyncClient connection pool. Tests
    # don't catch this because respx short-circuits the transport, but
    # production callers must hold the client across requests. See
    # docs/superpowers/plans/2026-04-27-nanormm-trmm-mcp.md Task 18.

    def __init__(self, base_url: str, token: str, *, timeout: float = 15.0):
        # TRMM accepts two auth schemes globally (see DRF DEFAULT_AUTHENTICATION_CLASSES):
        # knox.auth.TokenAuthentication ("Authorization: Token ...") for user logins,
        # and tacticalrmm.auth.APIAuthentication ("X-API-KEY: ...") for service callers.
        # nanormm is a service, so we use the X-API-KEY scheme — keys are long-lived,
        # bound to a TRMM user, and inherit that user's permissions.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-API-KEY": token},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> "TrmmClient":
        s = Settings()
        return cls(base_url=s.trmm_api_base, token=s.trmm_api_token)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise TrmmApiError(0, path, f"network error: {e}") from e
        self._raise_for_status(resp)
        try:
            return resp.json()
        except _json.JSONDecodeError as e:
            raise TrmmApiError(
                resp.status_code, str(resp.request.url), f"invalid JSON in response: {e}"
            ) from e

    async def post(self, path: str, *, json: Any = None) -> Any:
        try:
            resp = await self._client.post(path, json=json)
        except httpx.HTTPError as e:
            raise TrmmApiError(0, path, f"network error: {e}") from e
        self._raise_for_status(resp)
        if not resp.content:
            return None
        try:
            return resp.json()
        except _json.JSONDecodeError as e:
            raise TrmmApiError(
                resp.status_code, str(resp.request.url), f"invalid JSON in response: {e}"
            ) from e

    async def patch(self, path: str, *, json: Any = None) -> Any:
        try:
            resp = await self._client.patch(path, json=json)
        except httpx.HTTPError as e:
            raise TrmmApiError(0, path, f"network error: {e}") from e
        self._raise_for_status(resp)
        if not resp.content:
            return None
        try:
            return resp.json()
        except _json.JSONDecodeError as e:
            raise TrmmApiError(
                resp.status_code, str(resp.request.url), f"invalid JSON in response: {e}"
            ) from e

    async def delete(self, path: str) -> Any:
        try:
            resp = await self._client.delete(path)
        except httpx.HTTPError as e:
            raise TrmmApiError(0, path, f"network error: {e}") from e
        self._raise_for_status(resp)
        if not resp.content:
            return None
        try:
            return resp.json()
        except _json.JSONDecodeError as e:
            raise TrmmApiError(
                resp.status_code, str(resp.request.url), f"invalid JSON in response: {e}"
            ) from e

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        url = str(resp.request.url)
        body = resp.text[:500]
        if resp.status_code in (401, 403):
            raise TrmmAuthError(resp.status_code, url, body)
        if resp.status_code == 404:
            raise TrmmNotFoundError(resp.status_code, url, body)
        raise TrmmApiError(resp.status_code, url, body)
