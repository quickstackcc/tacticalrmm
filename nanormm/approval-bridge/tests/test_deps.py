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
