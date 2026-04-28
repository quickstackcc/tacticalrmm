from trmm_mcp.server import build_dispatcher as _build_dispatcher
from trmm_mcp.tools._base import Dispatcher

from .settings import BridgeSettings


def build_dispatcher_for_bridge(settings: BridgeSettings) -> Dispatcher:
    """Construct the dispatcher the bridge will share with trmm-mcp.

    Delegates to trmm-mcp's `build_dispatcher`, passing the trmm-mcp settings
    we composed in BridgeSettings. The bridge ignores the registry/client
    return values — it only needs the dispatcher to call `dispatch.resume()`
    on approval.
    """
    dispatcher, _registry, _trmm = _build_dispatcher(settings.trmm)
    return dispatcher
