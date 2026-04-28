import hmac

from fastapi import Depends, Header, HTTPException, status

from .settings import BridgeSettings


def verify_bearer(settings: BridgeSettings):
    """Build a FastAPI dependency that validates the Bearer header against
    `settings.api_key`. Returns the dependency callable for use in route
    signatures: ``def route(_=verify_bearer(settings))``.

    Comparison is constant-time to defeat timing attacks; mismatches and
    malformed headers all return a generic 401 with no detail (don't leak
    whether the auth header was missing vs. wrong).
    """
    expected = settings.api_key

    async def _dep(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return Depends(_dep)
