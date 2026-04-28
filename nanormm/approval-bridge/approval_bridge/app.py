from fastapi import FastAPI

from .auth import verify_bearer
from .deps import build_dispatcher_for_bridge
from .routes import authed_router, public_router
from .settings import BridgeSettings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    if settings is None:
        settings = BridgeSettings()

    app = FastAPI(title="nanormm approval-bridge", version="0.1.0")
    app.state.settings = settings
    app.state.dispatcher = build_dispatcher_for_bridge(settings)

    app.include_router(public_router)
    app.include_router(authed_router, dependencies=[verify_bearer(settings)])

    return app
