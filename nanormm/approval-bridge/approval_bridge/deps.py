from trmm_mcp.server import build_dispatcher as _build_dispatcher
from trmm_mcp.tools._base import Dispatcher

from .inject_client import InjectClient
from .settings import BridgeSettings


def build_dispatcher_for_bridge(settings: BridgeSettings) -> Dispatcher:
    """Construct the dispatcher with the inject_client wired in.

    The dispatcher built here is shared by both the bridge's MCP HTTP path
    (which sets X-Nanoclaw-Session header → contextvar → session_id) and
    the bridge's /api/nanoclaw/actions/execute/ path (which calls
    dispatcher.resume()). Only the HUMAN_APPROVAL branch consults
    inject_client; resume() doesn't.
    """
    dispatcher, _registry, _trmm = _build_dispatcher(settings.trmm)
    dispatcher._inject = InjectClient(settings.nanoclaw_internal_url)
    return dispatcher
