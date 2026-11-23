"""`reportback` command line. Ingest/index/link subcommands arrive with their phases."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from reportback import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reportback", description=__doc__)
    parser.add_argument("--version", action="version", version=f"reportback {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API locally")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        uvicorn.run("reportback.api.app:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
