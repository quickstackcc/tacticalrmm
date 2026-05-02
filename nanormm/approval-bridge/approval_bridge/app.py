import contextlib

from fastapi import FastAPI
from starlette.routing import Mount

from .auth import verify_bearer
from .deps import build_dispatcher_for_bridge
from .mcp_app import build_mcp_starlette_app
from .routes import authed_router, public_router
from .settings import BridgeSettings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    """Application factory. Builds the Dispatcher once and stashes it on
    `app.state.dispatcher` so route handlers can reach it via Request.

    Pass an explicit `settings` for tests; production calls with no args and
    BridgeSettings() reads from the environment.
    """
    if settings is None:
        settings = BridgeSettings()

    # Build the dispatcher with inject_client wired, then pass it to the
    # MCP app so tool calls have access to the inject endpoint.
    dispatcher = build_dispatcher_for_bridge(settings)
    mcp_app = build_mcp_starlette_app(dispatcher=dispatcher)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        # MCP streamable-http needs its session manager running for the
        # lifetime of the parent app. The mounted app's own lifespan does
        # this; we just need to delegate to it.
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(
        title="nanormm approval-bridge",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.dispatcher = dispatcher

    app.include_router(public_router)
    app.include_router(authed_router, dependencies=[verify_bearer(settings)])

    # Mount MCP at the configured path (default `/mcp`).
    app.routes.append(Mount(settings.mcp_path, app=mcp_app))

    return app
