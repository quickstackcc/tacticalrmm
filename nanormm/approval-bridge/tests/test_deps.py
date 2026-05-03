from approval_bridge.deps import build_dispatcher_for_bridge


def test_build_dispatcher_returns_configured_instance(bridge_settings):
    """Smoke test: factory returns a Dispatcher with the expected methods."""
    dispatcher = build_dispatcher_for_bridge(bridge_settings)

    assert hasattr(dispatcher, "dispatch")
    assert hasattr(dispatcher, "resume")
    assert hasattr(dispatcher, "recover")
    # The shared Redis is fakeredis from the bridge_settings fixture, so a
    # round-trip through the approval registry should work end-to-end.
    assert dispatcher._approvals.get("act_nope") is None


def test_dispatcher_has_inject_client_wired(bridge_settings):
    from approval_bridge.deps import build_dispatcher_for_bridge
    from approval_bridge.inject_client import InjectClient

    dispatcher = build_dispatcher_for_bridge(bridge_settings)
    assert isinstance(dispatcher._inject, InjectClient)
    assert dispatcher._inject._base == "http://127.0.0.1:8765"
