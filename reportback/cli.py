"""`reportback` command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from reportback import __version__
from reportback.config import Settings, get_settings


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("reportback.api.app:app", host=args.host, port=args.port)
    return 0


def cmd_db_migrate(settings: Settings) -> int:
    from reportback.db import connect, migrate

    with connect(settings) as conn:
        applied = migrate(conn)
    print("applied:", applied or "nothing (up to date)")
    return 0


def _log_store(settings: Settings) -> Any:
    from reportback.ingest.report_log import SnapshotStore

    return SnapshotStore(settings.snapshots_dir / "report_request_log")


def cmd_snapshot(settings: Settings) -> int:
    from reportback.ingest.ckan import CkanClient
    from reportback.ingest.http import PoliteClient
    from reportback.ingest.report_log import snapshot_report_log

    store = _log_store(settings)
    with PoliteClient(settings) as http:
        run = snapshot_report_log(http, CkanClient(http, settings.ckan_base_url), store)
    for s in run.saved:
        print(f"NEW       {s.term} {s.as_of} {s.snapshot_id[:12]} rows={s.n_rows} -> {s.path}")
    for s in run.unchanged:
        print(f"unchanged {s.term} {s.as_of} {s.snapshot_id[:12]}")
    print("heartbeat written:", run.heartbeat_written)
    return 0


def cmd_ingest(settings: Settings, args: argparse.Namespace) -> int:
    from reportback.db import connect

    with connect(settings) as conn:
        if args.source == "log":
            from reportback.ingest.divisions import default_mapper
            from reportback.ingest.report_log import ingest_report_log

            stats = ingest_report_log(conn, _log_store(settings), default_mapper())
            _print({k: v for k, v in stats.__dict__.items() if k != "unparsed_item_numbers"})
            if stats.unparsed_item_numbers:
                print("unparsed item numbers:", stats.unparsed_item_numbers)
        elif args.source == "votes":
            from reportback.ingest.ckan import CkanClient
            from reportback.ingest.http import PoliteClient
            from reportback.ingest.votes import ingest_votes

            with PoliteClient(settings) as http:
                _print(ingest_votes(conn, http, CkanClient(http, settings.ckan_base_url), args.term))
        elif args.source == "tmmis":
            from reportback.ingest.http import PoliteClient
            from reportback.ingest.tmmis import crawl_items

            with PoliteClient(settings) as http:
                _print(crawl_items(conn, http, term=args.term, limit=args.limit, reparse=args.reparse))
        elif args.source == "background":
            from reportback.ingest.background import ingest_background
            from reportback.ingest.http import PoliteClient

            with PoliteClient(settings) as http:
                _print(
                    ingest_background(
                        conn, http, limit=args.limit, kinds=tuple(args.kinds.split(",")), reparse=args.reparse
                    )
                )
        elif args.source == "citegraph":
            from reportback.ingest.citegraph import build_citegraph

            _print(build_citegraph(conn))
    return 0


def cmd_coverage(settings: Settings, args: argparse.Namespace) -> int:
    from reportback.coverage import write_coverage_report
    from reportback.db import connect

    with connect(settings) as conn:
        path = write_coverage_report(conn, settings, args.out)
    print("wrote", path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reportback", description=__doc__)
    parser.add_argument("--version", action="version", version=f"reportback {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API locally")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    db = sub.add_parser("db", help="database maintenance")
    db.add_argument("action", choices=["migrate"])

    sub.add_parser("snapshot", help="archive new versions of the Report Request Log")

    ing = sub.add_parser("ingest", help="load a source into Postgres")
    ing.add_argument("source", choices=["log", "votes", "tmmis", "background", "citegraph"])
    ing.add_argument("--term", default="2022-2026", help="council term (votes, tmmis seeds)")
    ing.add_argument("--limit", type=int, default=None, help="max items/files to fetch this run")
    ing.add_argument(
        "--reparse",
        action="store_true",
        help="tmmis/background: re-parse already-fetched pages/files from cache",
    )
    ing.add_argument(
        "--kinds",
        default="report,letter,briefing_note,answer",
        help="background: doc kinds to download (comma-separated)",
    )

    cov = sub.add_parser("coverage", help="write the coverage report")
    cov.add_argument("--out", default="docs/COVERAGE.md")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return cmd_serve(args)
    settings = get_settings()
    if args.command == "db":
        return cmd_db_migrate(settings)
    if args.command == "snapshot":
        return cmd_snapshot(settings)
    if args.command == "ingest":
        return cmd_ingest(settings, args)
    if args.command == "coverage":
        return cmd_coverage(settings, args)
    parser.error(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
