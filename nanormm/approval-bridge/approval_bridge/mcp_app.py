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

from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route

from trmm_mcp.server import build_server


def build_mcp_starlette_app() -> Starlette:
    """Construct the Starlette ASGI app that serves trmm-mcp over HTTP.

    Uses `stateless=True` and `json_response=True` so:
    - No session state is required between requests (simplifies test setup).
    - Responses are plain JSON rather than SSE, making assertions straightforward.

    The session manager's `run()` lifespan is wired into the returned app so
    mounting it into FastAPI's lifespan via `mcp_app.router.lifespan_context`
    starts and stops the session manager correctly.
    """
    server = build_server()

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

    return Starlette(
        routes=[Route("/", endpoint=asgi_handler)],
        lifespan=lifespan,
    )
