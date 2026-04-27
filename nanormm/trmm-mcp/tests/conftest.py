import os
from pathlib import Path
from unittest.mock import patch

import fakeredis
import pytest
from pytest_postgresql import factories  # noqa: F401  (registers `postgresql` fixture)


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


def _load_migrations(**kwargs):
    """Initializer for pytest-postgresql: applies our schema."""
    import psycopg

    migrations_dir = Path(__file__).resolve().parents[1] / "trmm_mcp" / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    with psycopg.connect(**kwargs) as conn:
        for f in sql_files:
            with conn.cursor() as cur:
                cur.execute(f.read_text())
        conn.commit()


@pytest.fixture
def audit_dsn(postgresql) -> str:
    """Postgres DSN with nanormm schema applied."""
    info = postgresql.info
    dsn = f"postgresql://{info.user}:@{info.host}:{info.port}/{info.dbname}"
    _load_migrations(
        host=info.host,
        port=info.port,
        user=info.user,
        dbname=info.dbname,
    )
    return dsn
