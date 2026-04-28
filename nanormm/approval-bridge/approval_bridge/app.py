from fastapi import FastAPI

from .deps import build_dispatcher_for_bridge
from .routes import router
from .settings import BridgeSettings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    """Application factory. Builds the Dispatcher once and stashes it on
    `app.state.dispatcher` so route handlers can reach it via Request.

    Pass an explicit `settings` for tests; production calls with no args and
    BridgeSettings() reads from the environment.
    """
    if settings is None:
        settings = BridgeSettings()

    app = FastAPI(title="nanormm approval-bridge", version="0.1.0")
    app.state.settings = settings
    app.state.dispatcher = build_dispatcher_for_bridge(settings)

    app.include_router(router)

    return app
