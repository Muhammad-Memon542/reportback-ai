from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from reportback.config import Settings
from reportback.db import Conn, migrate

FIXTURES = Path(__file__).parent / "fixtures"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings isolated from the developer's .env and real data dir."""
    return Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read env vars; keep a developer's LLM_*/DATABASE_URL out of unit tests."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(scope="session")
def db_url() -> str:
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set (Postgres tests skipped)")
    return url


@pytest.fixture
def db(db_url: str) -> Iterator[Conn]:
    """A freshly migrated database. Drops this role's tables first, keeps extensions."""
    conn = psycopg.connect(db_url, autocommit=True)
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user"
    ).fetchall()
    for (name,) in rows:
        conn.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')
    conn.autocommit = False
    migrate(conn)
    conn.commit()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
