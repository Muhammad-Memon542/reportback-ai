from __future__ import annotations

from reportback.db import Conn, migrate, migration_files, table_counts


def test_migration_files_are_numbered_and_unique() -> None:
    files = migration_files()
    assert files and files[0].name == "001_init.sql"


def test_migrate_is_idempotent(db: Conn) -> None:
    assert migrate(db) == []  # fixture already applied everything
    counts = table_counts(db, ["directive", "item", "doc", "cites", "vote"])
    assert counts == {"directive": 0, "item": 0, "doc": 0, "cites": 0, "vote": 0}
