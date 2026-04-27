import os
from unittest.mock import patch

import pytest


def test_settings_loads_required_fields():
    from trmm_mcp.settings import Settings

    env = {
        "TRMM_API_BASE": "https://api.example.com",
        "TRMM_API_TOKEN": "tok123",
        "NANORMM_POLICY_PATH": "/etc/nanormm/policy.yaml",
        "NANORMM_REDIS_URL": "redis://localhost:6379/11",
        "NANORMM_AUDIT_DSN": "postgresql://u:p@h:5432/d",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings()

    assert s.trmm_api_base == "https://api.example.com"
    assert s.trmm_api_token == "tok123"  # noqa: S105 - test fixture value
    assert str(s.policy_path) == "/etc/nanormm/policy.yaml"
    assert s.redis_url == "redis://localhost:6379/11"
    assert s.audit_dsn == "postgresql://u:p@h:5432/d"
    assert s.pending_action_ttl_seconds == 1800  # 30-min default


def test_settings_strips_trailing_slash_on_api_base():
    from trmm_mcp.settings import Settings

    env = {
        "TRMM_API_BASE": "https://api.example.com/",
        "TRMM_API_TOKEN": "tok",
        "NANORMM_POLICY_PATH": "/x.yaml",
        "NANORMM_REDIS_URL": "redis://localhost:6379/11",
        "NANORMM_AUDIT_DSN": "postgresql://u:p@h/d",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings()

    assert s.trmm_api_base == "https://api.example.com"


def test_settings_missing_required_raises():
    from pydantic import ValidationError  # noqa: I001 - kept inside test for isolation
    from trmm_mcp.settings import Settings

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError):
            Settings()
