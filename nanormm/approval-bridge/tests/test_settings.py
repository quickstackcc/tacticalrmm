import pytest
from pydantic import ValidationError

from approval_bridge.settings import BridgeSettings


@pytest.fixture
def base_env(monkeypatch):
    """Set every shared trmm-mcp var so only bridge-specific fields drive
    the test outcomes.
    """
    monkeypatch.setenv("TRMM_API_BASE", "https://api.example.com")
    monkeypatch.setenv("TRMM_API_TOKEN", "trmm-token")
    monkeypatch.setenv("NANORMM_POLICY_PATH", "/etc/nanormm/policy.yaml")
    monkeypatch.setenv("NANORMM_REDIS_URL", "redis://localhost:6379/11")
    monkeypatch.setenv(
        "NANORMM_AUDIT_DSN",
        "postgresql://nanormm:pw@localhost:5432/nanormm",
    )


def test_loads_required_env_vars(monkeypatch, base_env):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "secret-token")

    s = BridgeSettings()

    assert s.api_key == "secret-token"
    assert s.host == "0.0.0.0"  # default  # noqa: S104
    assert s.port == 8000  # default

    # Trmm-mcp shared settings exposed via .trmm
    assert s.trmm.trmm_api_base == "https://api.example.com"
    assert s.trmm.trmm_api_token == "trmm-token"
    assert str(s.trmm.policy_path).endswith("policy.yaml")
    assert s.trmm.redis_url.startswith("redis://")
    assert s.trmm.audit_dsn.startswith("postgresql://")
    assert s.trmm.pending_action_ttl_seconds == 1800


def test_missing_api_key_raises(monkeypatch, base_env):
    monkeypatch.delenv("NANORMM_BRIDGE_API_KEY", raising=False)

    with pytest.raises(ValidationError) as exc:
        BridgeSettings()

    msg = str(exc.value).lower()
    assert "api_key" in msg or "field required" in msg or "nanormm_bridge_api_key" in msg


def test_overrides_host_and_port(monkeypatch, base_env):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "x")
    monkeypatch.setenv("NANORMM_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("NANORMM_BRIDGE_PORT", "9001")

    s = BridgeSettings()
    assert s.host == "127.0.0.1"
    assert s.port == 9001


def test_nanoclaw_internal_url_default(monkeypatch, base_env):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "x")
    s = BridgeSettings()
    assert s.nanoclaw_internal_url == "http://127.0.0.1:8765"


def test_nanoclaw_internal_url_overridable(monkeypatch, base_env):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "x")
    monkeypatch.setenv("NANOCLAW_INTERNAL_URL", "http://example.local:9999")
    s = BridgeSettings()
    assert s.nanoclaw_internal_url == "http://example.local:9999"
