from unittest.mock import patch


def test_main_invokes_uvicorn_with_app_factory(bridge_settings):
    """Smoke test: the entrypoint module wires uvicorn.run with the app factory."""
    with patch("uvicorn.run") as mock_run:
        from approval_bridge.__main__ import main

        main()

        assert mock_run.called
        args, kwargs = mock_run.call_args
        # First positional should be either the app instance or import string
        assert args or "app" in kwargs
        assert kwargs.get("host") == bridge_settings.host
        assert kwargs.get("port") == bridge_settings.port
