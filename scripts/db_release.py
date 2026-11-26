"""Publish the database as a GitHub Release asset, or fetch and restore the latest one.

    uv run python scripts/db_release.py publish            # dump -> db-snapshot branch -> release
    uv run python scripts/db_release.py fetch [--tag TAG]  # download -> verify sha256 -> restore

`publish` force-pushes the dump to the `db-snapshot` branch (holding only the latest dump) and
dispatches `.github/workflows/publish-db-release.yml`, which creates the release with the repo's
own token: releases cannot be created directly from a Claude Code session.

Why (DECISIONS D21): the loaded database (crawl + parse ~3 h) lives in an ephemeral container.
Release assets are free, survive the container and the Azure credit, and need no new service.
Uses the GitHub REST API through `gh api` (GraphQL is unavailable in Claude Code sessions).
The repository is public, so the dump is public: it holds only City open data (Open Government
Licence - Toronto), with public communications never stored and emails/phones scrubbed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from reportback.config import get_settings
from reportback.db import connect, dump, restore, table_counts

ROOT = Path(__file__).resolve().parent.parent
TABLES = [
    "log_snapshot",
    "directive",
    "log_entry",
    "vote",
    "item",
    "item_consideration",
    "doc",
    "doc_section",
    "cites",
]
ATTRIBUTION = "Contains information licensed under the Open Government Licence – Toronto."


def gh(*args: str, input_file: Path | None = None) -> str:
    cmd = ["gh", "api", *args]
    if input_file is not None:
        cmd += ["--input", str(input_file)]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def repo_slug() -> str:
    url = subprocess.run(
        ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    if not m:
        raise SystemExit(f"cannot parse GitHub repo from {url}")
    return m.group(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


BRANCH = "db-snapshot"
WORKFLOW = "publish-db-release.yml"


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def publish(tag: str | None) -> int:
    """Dump -> force-push the dump to the `db-snapshot` branch -> dispatch the release workflow."""
    import shutil
    import tempfile

    settings = get_settings()
    today = datetime.now(UTC).date().isoformat()
    tag = tag or f"data-{today}"
    out = ROOT / "data" / "releases" / f"reportback-db-{today}.pgdump"
    dump(settings, out)
    digest = sha256(out)
    with connect(settings) as conn:
        counts = table_counts(conn, TABLES)
        migrations = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY version")]
    commit = git("rev-parse", "--short", "HEAD")
    rows = "\n".join(f"| `{t}` | {n:,} |" for t, n in counts.items())
    notes = f"""Database snapshot of ReportBack TO, made {today} at commit `{commit}`.

Restore into Postgres 16 with `vector` and `pg_trgm` installed:

```bash
uv run python scripts/db_release.py fetch --tag {tag}
```

| table | rows |
|---|---|
{rows}

Migrations applied: {", ".join(migrations)}.
File: `{out.name}` ({out.stat().st_size / 1e6:.1f} MB, pg_dump custom format).
sha256: {digest}

What is inside: the City Clerk's Report Request Log (both terms), the 2022–2026 voting record, parsed
TMMIS agenda items, staff-report text split by section, and the citation graph. Letters and deputations
from members of the public are not stored (counts only); emails and phone numbers are removed from text.
See docs/COVERAGE.md and docs/DATASHEET.md.

{ATTRIBUTION} ReportBack is not an official City of Toronto service.
"""
    manifest = {"tag": tag, "file": out.name, "sha256": digest, "bytes": out.stat().st_size,
                "commit": commit, "made": today, "counts": counts, "migrations": migrations}  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "snapshot"
        git("worktree", "add", "--detach", str(work))
        try:
            git("checkout", "--orphan", BRANCH + "-tmp", cwd=work)
            git("rm", "-rf", "--quiet", ".", cwd=work)
            shutil.copy2(out, work / out.name)
            (work / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
            (work / "NOTES.md").write_text(notes)
            git("add", "-A", cwd=work)
            git(
                "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", f"Database snapshot {today}", cwd=work
            )
            git("push", "--force", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=work)
        finally:
            git("worktree", "remove", "--force", str(work))
            subprocess.run(["git", "-C", str(ROOT), "branch", "-D", BRANCH + "-tmp"], capture_output=True)
    slug = repo_slug()
    gh("--method", "POST", f"repos/{slug}/actions/workflows/{WORKFLOW}/dispatches", "-f", "ref=main")
    print(f"pushed {out.name} ({digest[:12]}) to branch {BRANCH}; release workflow dispatched for {tag}")
    print(f"  follow it at https://github.com/{slug}/actions/workflows/{WORKFLOW}")
    return 0


def fetch(tag: str | None) -> int:
    """Download the newest (or given) data release, verify sha256, restore. Falls back to the branch."""
    slug = repo_slug()
    releases = json.loads(gh(f"repos/{slug}/releases?per_page=100"))
    data = [
        r for r in releases if r["tag_name"].startswith("data-") and (tag is None or r["tag_name"] == tag)
    ]
    out_dir = ROOT / "data" / "releases"
    out_dir.mkdir(parents=True, exist_ok=True)
    if data:
        rel = max(data, key=lambda r: r["tag_name"])
        asset = next(a for a in rel["assets"] if a["name"].endswith(".pgdump"))
        expected = re.search(r"sha256: ([0-9a-f]{64})", rel["body"] or "")
        out = out_dir / asset["name"]
        with out.open("wb") as f:
            url = f"repos/{slug}/releases/assets/{asset['id']}"
            subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream", url], check=True, stdout=f)
        source = rel["tag_name"]
        want = expected.group(1) if expected else None
    else:
        git("fetch", "--depth=1", "origin", BRANCH)
        manifest = json.loads(git("show", "FETCH_HEAD:MANIFEST.json"))
        out = out_dir / manifest["file"]
        with out.open("wb") as f:
            subprocess.run(
                ["git", "-C", str(ROOT), "show", f"FETCH_HEAD:{manifest['file']}"], check=True, stdout=f
            )
        source, want = f"branch {BRANCH}", manifest["sha256"]
    digest = sha256(out)
    if want and digest != want:
        raise SystemExit(f"sha256 mismatch: got {digest}, expected {want}")
    restore(get_settings(), out)
    print(f"restored {source} ({out.name}, sha256 verified)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["publish", "fetch"])
    ap.add_argument("--tag")
    args = ap.parse_args()
    return publish(args.tag) if args.action == "publish" else fetch(args.tag)


if __name__ == "__main__":
    sys.exit(main())
