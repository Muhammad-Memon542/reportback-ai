from __future__ import annotations

from reportback.db import Conn, migrate, migration_files, table_counts


def test_migration_files_are_numbered_and_unique() -> None:
    files = migration_files()
    assert files and files[0].name == "001_init.sql"
    assert [f.name[:3] for f in files] == [f"{i:03d}" for i in range(1, len(files) + 1)]  # no gaps


def test_migrate_is_idempotent(db: Conn) -> None:
    assert migrate(db) == []  # fixture already applied everything
    counts = table_counts(db, ["directive", "item", "doc", "cites", "vote"])
    assert counts == {"directive": 0, "item": 0, "doc": 0, "cites": 0, "vote": 0}


def test_coverage_report_renders_on_empty_db(db: Conn) -> None:
    from reportback.coverage import build_report

    md = build_report(db)
    assert md.startswith("# Coverage report")
    assert "Open Government Licence – Toronto" in md
    assert "## 6. Preview" in md
