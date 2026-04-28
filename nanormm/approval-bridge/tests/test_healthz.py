from fastapi.testclient import TestClient

from approval_bridge.app import create_app


def test_healthz_returns_200(bridge_settings):
    app = create_app(bridge_settings)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_does_not_require_auth(bridge_settings):
    app = create_app(bridge_settings)
    client = TestClient(app)
    # No Authorization header
    r = client.get("/healthz")
    assert r.status_code == 200
