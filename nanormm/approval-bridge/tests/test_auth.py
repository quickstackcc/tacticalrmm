import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from approval_bridge.auth import verify_bearer
from approval_bridge.settings import BridgeSettings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "the-secret")
    monkeypatch.setenv("NANORMM_REDIS_URL", "redis://x/0")
    monkeypatch.setenv("NANORMM_AUDIT_DSN", "postgresql://x/x")
    monkeypatch.setenv("TRMM_API_BASE", "https://x")
    monkeypatch.setenv("TRMM_API_TOKEN", "x")
    monkeypatch.setenv("NANORMM_POLICY_PATH", "/etc/policy.yaml")
    return BridgeSettings()


@pytest.fixture
def client(settings):
    app = FastAPI()

    @app.get("/protected")
    def protected(_: None = verify_bearer(settings)):
        return {"ok": True}

    return TestClient(app)


def test_missing_authorization_header_returns_401(client):
    r = client.get("/protected")
    assert r.status_code == 401


def test_wrong_scheme_returns_401(client):
    r = client.get("/protected", headers={"Authorization": "Token the-secret"})
    assert r.status_code == 401


def test_wrong_secret_returns_401(client):
    r = client.get("/protected", headers={"Authorization": "Bearer not-the-secret"})
    assert r.status_code == 401


def test_correct_secret_passes(client):
    r = client.get("/protected", headers={"Authorization": "Bearer the-secret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
