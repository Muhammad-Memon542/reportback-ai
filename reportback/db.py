"""Postgres access and the numbered-migration runner (backend/migrations/NNN_name.sql)."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
from psycopg.rows import TupleRow

from reportback.config import Settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "backend" / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

Conn = psycopg.Connection[TupleRow]


class DatabaseNotConfiguredError(RuntimeError):
    pass


def connect(settings: Settings, url: str | None = None) -> Conn:
    """Autocommit connection: each `with conn.transaction():` block is a real BEGIN/COMMIT.

    (Without autocommit, the first statement opens an implicit transaction and every later
    `conn.transaction()` is only a SAVEPOINT, so a long crawl would commit nothing until the
    very end - see LESSONS L9.)
    """
    dsn = url or settings.database_url
    if not dsn:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set (see .env.example)")
    return psycopg.connect(dsn, autocommit=True)


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(p for p in directory.iterdir() if _MIGRATION_RE.match(p.name))
    versions = [p.name[:3] for p in files]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"duplicate migration numbers in {directory}")
    return files


def migrate(conn: Conn, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, each in its own transaction. Returns applied file names."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, name text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []
    for path in migration_files(directory):
        version = path.name[:3]
        if version in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, path.name)
            )
        applied.append(path.name)
    return applied


def table_counts(conn: Conn, tables: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tables:
        if not re.fullmatch(r"[a-z_]+", t):
            raise ValueError(t)
        row = conn.execute(f"SELECT count(*) FROM {t}").fetchone()  # noqa: S608 (t validated above)
        out[t] = int(row[0]) if row else 0
    return out
