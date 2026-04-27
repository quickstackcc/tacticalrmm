import os
from unittest.mock import patch

import fakeredis
import pytest


@pytest.fixture
def trmm_env():
    """Minimal env for Settings to load successfully."""
    env = {
        "TRMM_API_BASE": "https://api.test",
        "TRMM_API_TOKEN": "test-token",
        "NANORMM_POLICY_PATH": "/tmp/policy.yaml",  # noqa: S108 - test fixture path, never opened
        "NANORMM_REDIS_URL": "redis://localhost:6379/15",
        "NANORMM_AUDIT_DSN": "postgresql://u:p@h:5432/d",
    }
    with patch.dict(os.environ, env, clear=True):
        yield env


@pytest.fixture
def fake_redis():
    """In-memory redis stand-in."""
    return fakeredis.FakeStrictRedis(decode_responses=True)
