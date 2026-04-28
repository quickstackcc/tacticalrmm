import textwrap
from pathlib import Path

import fakeredis
import pytest
import respx
from pytest_postgresql import factories

from approval_bridge.settings import BridgeSettings

# pytest-postgresql spins up an ephemeral Postgres. The `postgresql` fixture
# is a live psycopg connection; we use it to set up the schema then expose
# its DSN as `audit_db_dsn` so the bridge's deps.py can reach it.
postgresql_proc = factories.postgresql_proc(port=None)
postgresql = factories.postgresql("postgresql_proc")


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    """Minimal policy: one auto read-tool, one human_approval write-tool."""
    f = tmp_path / "policy.yaml"
    f.write_text(
        textwrap.dedent("""
        version: 1
        default: human_approval
        tools:
          list_alerts: auto
          kill_process: human_approval
    """).strip()
    )
    return f


@pytest.fixture
def fake_redis_client():
    """Shared in-memory Redis. trmm-mcp uses decode_responses=True, so match
    that here for parity with production behavior.
    """
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def audit_db_dsn(postgresql) -> str:
    """Install the nanormm_actions schema on the ephemeral DB and return DSN.
    Schema mirrors what trmm-mcp's audit migration installs in production.
    """
    cur = postgresql.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nanormm_actions (
            action_id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            args JSONB NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            policy_decision TEXT NOT NULL,
            pending_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_by TEXT,
            approved_at TIMESTAMPTZ,
            rejected_by TEXT,
            rejected_at TIMESTAMPTZ,
            reject_reason TEXT,
            executed_at TIMESTAMPTZ,
            result JSONB
        )
    """)
    postgresql.commit()
    info = postgresql.info
    return f"postgresql://{info.user}:@{info.host}:{info.port}/{info.dbname}"


@pytest.fixture
def mock_trmm():
    """respx mock for the TRMM REST API. Tests add specific routes."""
    with respx.mock(base_url="https://trmm.test", assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def bridge_settings(monkeypatch, policy_file, fake_redis_client, audit_db_dsn) -> BridgeSettings:
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "test-secret")
    monkeypatch.setenv("TRMM_API_BASE", "https://trmm.test")
    monkeypatch.setenv("TRMM_API_TOKEN", "trmm-test-token")
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(policy_file))
    monkeypatch.setenv("NANORMM_REDIS_URL", "redis://fake")
    monkeypatch.setenv("NANORMM_AUDIT_DSN", audit_db_dsn)
    monkeypatch.setenv("NANORMM_PENDING_TTL_SECONDS", "60")

    # Replace `redis.Redis.from_url` so build_dispatcher gets fakeredis.
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: fake_redis_client)

    return BridgeSettings()
