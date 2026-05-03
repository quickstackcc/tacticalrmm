"""MCP HTTP transport for the approval-bridge.

Mounts trmm-mcp's lowlevel Server (with all tools registered) as a Starlette
sub-application using the streamable-http transport from the Python MCP SDK.
The same Server instance backs both stdio (`python -m trmm_mcp`) and the
bridge's HTTP endpoint — single source of truth for the MCP surface.

Note: The Python MCP SDK's `streamable_http_app()` convenience method lives
on `FastMCP`, not on the lowlevel `Server`. We replicate its logic here,
wiring `StreamableHTTPSessionManager` + `StreamableHTTPASGIApp` directly
against the lowlevel Server returned by `build_server()`.
"""

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import ASGIApp

from trmm_mcp.server import build_server

# Set by _SessionHeaderMiddleware on each HTTP request, read by trmm-mcp's
# call_tool handler so the Dispatcher knows which nanoclaw session to inject
# the approval card into.
SESSION_ID_VAR: ContextVar[str | None] = ContextVar(
    "nanoclaw_session_id", default=None
)


class _SessionHeaderMiddleware:
    """ASGI middleware that copies X-Nanoclaw-Session into SESSION_ID_VAR
    for the duration of each HTTP request, resetting on completion.
    """

    def __init__(self, app):
        self.app = app

    def __getattr__(self, name):
        # Delegate attribute access to the wrapped app so lifespan_context
        # and other router attributes are accessible through the middleware.
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            sid = headers.get(b"x-nanoclaw-session")
            token = SESSION_ID_VAR.set(sid.decode() if sid else None)
            try:
                await self.app(scope, receive, send)
            finally:
                SESSION_ID_VAR.reset(token)
        else:
            await self.app(scope, receive, send)


def build_mcp_starlette_app(dispatcher=None) -> ASGIApp:
    """Construct the Starlette ASGI app that serves trmm-mcp over HTTP.

    Uses `stateless=True` and `json_response=True` so:
    - No session state is required between requests (simplifies test setup).
    - Responses are plain JSON rather than SSE, making assertions straightforward.

    The session manager's `run()` lifespan is wired into the returned app so
    mounting it into FastAPI's lifespan via `mcp_app.router.lifespan_context`
    starts and stops the session manager correctly.

    Pass `dispatcher` if you have a pre-built Dispatcher with inject_client
    wired (e.g., from approval-bridge's deps.py). If None, a fresh dispatcher
    is constructed via build_server().
    """
    if dispatcher is None:
        server = build_server()
    else:
        # Use the provided dispatcher with inject_client already wired.
        # We need to construct the MCP server with the provided dispatcher
        # and the tool registry from it. Extract the registry from the old
        # server to avoid duplication.
        from mcp.server import Server
        from trmm_mcp.server import _register_all, _tool_descriptor

        old_server = build_server()
        mcp = Server("trmm-mcp")

        @mcp.list_tools()
        async def _list_tools():
            return [_tool_descriptor(name) for name in dispatcher._registry.tool_names()]

        @mcp.call_tool()
        async def _call_tool(name: str, arguments: dict):
            from mcp.types import TextContent

            # Read X-Nanoclaw-Session populated by the bridge's middleware.
            session_id: str | None = None
            try:
                session_id = SESSION_ID_VAR.get()
            except (NameError, LookupError):
                pass

            result = await dispatcher.dispatch(name, arguments, session_id=session_id)
            # Serialize the result to JSON string.
            import json

            return [TextContent(type="text", text=json.dumps(result, default=str, separators=(",", ":")))]

        @dataclass
        class TrmmMcpServer:
            mcp: Any
            tool_registry: Any
            dispatcher: Any
            trmm_client: Any

        server = TrmmMcpServer(
            mcp=mcp,
            tool_registry=dispatcher._registry,
            dispatcher=dispatcher,
            trmm_client=old_server.trmm_client,
        )

    session_manager = StreamableHTTPSessionManager(
        app=server.mcp,
        json_response=True,
        stateless=True,
    )

    asgi_handler = StreamableHTTPASGIApp(session_manager)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        routes=[Route("/", endpoint=asgi_handler)],
        lifespan=lifespan,
    )
    # Wrap so SESSION_ID_VAR is populated before the MCP handler runs.
    return _SessionHeaderMiddleware(starlette_app)
