from typing import Any

import httpx

from .exceptions import TrmmApiError, TrmmAuthError, TrmmNotFoundError
from .settings import Settings


class TrmmClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 15.0):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Token {token}"},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> "TrmmClient":
        s = Settings()
        return cls(base_url=s.trmm_api_base, token=s.trmm_api_token)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        self._raise_for_status(resp)
        return resp.json()

    async def post(self, path: str, *, json: Any = None) -> Any:
        resp = await self._client.post(path, json=json)
        self._raise_for_status(resp)
        return resp.json() if resp.content else None

    async def patch(self, path: str, *, json: Any = None) -> Any:
        resp = await self._client.patch(path, json=json)
        self._raise_for_status(resp)
        return resp.json() if resp.content else None

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
