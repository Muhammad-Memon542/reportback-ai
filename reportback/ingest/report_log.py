"""Report Request Log: snapshot archive + parsing + idempotent load into Postgres.

Snapshots (DECISIONS D10) live in data/snapshots/report_request_log/:
    MANIFEST.csv                      one row per archived file
    LAST_CHECKED                      date of the last check (heartbeat, at most every 30 days)
    <term>/<as_of>_<sha12>.xlsx       the Clerk's file, byte for byte
    <term>/latest.csv                 normalized view of the newest file, sorted for clean diffs
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import warnings
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from reportback.db import Conn
from reportback.ingest.ckan import CkanClient, Resource, normalize_header
from reportback.ingest.divisions import DivisionMapper, split_assignees
from reportback.ingest.http import PoliteClient
from reportback.ingest.ids import MeetingIndex, normalize_item_id, split_item_refs, term_years
from reportback.ingest.text import norm_ws
from reportback.ingest.timing import parse_timing

PACKAGE = "report-request-log-city-council-and-its-committees"
SHEET = "Main Data"
COLUMNS = {  # normalized header -> field
    "status": "status",
    "req. body": "req_body",
    "meeting date": "meeting_date",
    "item number": "item_number",
    "agenda title": "agenda_title",
    "directive": "directive",
    "rpt to:": "report_to",
    "report back timing": "timing",
    "assigned to": "assigned_to",
    "closed by": "closed_by",
    "date for order": "date_for_order",
}
REQUIRED = ("status", "meeting_date", "item_number", "directive", "assigned_to", "closed_by")
STATUS_NORM = {
    "closed": "closed",
    "ncf": "ncf",
    "not carried forward": "ncf",
    "open": "open",
    "due": "due",
    "ongoing": "ongoing",
    "pending": "pending",
}
MANIFEST_FIELDS = [
    "snapshot_id", "term", "resource_id", "resource_name", "last_modified",
    "as_of", "fetched_at", "path", "bytes", "n_rows",
]  # fmt: skip
HEARTBEAT_DAYS = 30


class LogFormatError(RuntimeError):
    pass


@dataclass(frozen=True)
class LogRow:
    row_no: int
    values: dict[str, Any]

    def get(self, key: str) -> Any:
        v = self.values.get(key)
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


def term_of(resource_name: str) -> str:
    m = re.search(r"(20\d\d)\s*-\s*(20\d\d)", resource_name)
    if not m:
        raise LogFormatError(f"cannot find a term in resource name {resource_name!r}")
    return f"{m.group(1)}-{m.group(2)}"


def read_workbook(content: bytes) -> list[LogRow]:
    import openpyxl

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            if SHEET not in wb.sheetnames:
                raise LogFormatError(f"sheet {SHEET!r} not found (have {wb.sheetnames})")
            rows = wb[SHEET].iter_rows(values_only=True)
            header = [normalize_header(c).lower() if c is not None else "" for c in next(rows, ())]
            idx = {COLUMNS[h]: i for i, h in enumerate(header) if h in COLUMNS}
            missing = [c for c in REQUIRED if c not in idx]
            if missing:
                raise LogFormatError(f"missing columns {missing}; header was {header}")
            out: list[LogRow] = []
            for n, r in enumerate(rows, start=1):
                if not any(c is not None and str(c).strip() for c in r):
                    continue
                out.append(LogRow(n, {f: (r[i] if i < len(r) else None) for f, i in idx.items()}))
            return out
        finally:
            wb.close()


def _cell_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return norm_ws(str(v))


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def directive_key(item_number: str, text: str) -> str:
    return hashlib.sha256(f"{item_number}|{norm_ws(text)}".encode()).hexdigest()[:20]


def status_norm(raw: str | None) -> str:
    if not raw:
        return "unknown"
    return STATUS_NORM.get(norm_ws(raw).lower(), "unknown")


# --------------------------------------------------------------------------- snapshot archive


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: str
    term: str
    resource_id: str
    resource_name: str
    last_modified: str
    as_of: date
    fetched_at: str
    path: str  # relative to the archive root
    bytes: int
    n_rows: int


@dataclass
class SnapshotStore:
    root: Path  # data/snapshots/report_request_log

    @property
    def manifest_path(self) -> Path:
        return self.root / "MANIFEST.csv"

    def manifest(self) -> list[SnapshotInfo]:
        if not self.manifest_path.exists():
            return []
        with self.manifest_path.open(newline="", encoding="utf-8") as f:
            return [
                SnapshotInfo(
                    snapshot_id=r["snapshot_id"],
                    term=r["term"],
                    resource_id=r["resource_id"],
                    resource_name=r["resource_name"],
                    last_modified=r["last_modified"],
                    as_of=date.fromisoformat(r["as_of"]),
                    fetched_at=r["fetched_at"],
                    path=r["path"],
                    bytes=int(r["bytes"]),
                    n_rows=int(r["n_rows"]),
                )
                for r in csv.DictReader(f)
            ]

    def save(self, resource: Resource, content: bytes, now: datetime) -> tuple[SnapshotInfo, bool]:
        """Archive `content` unless an identical file is already archived. Returns (info, is_new)."""
        sha = hashlib.sha256(content).hexdigest()
        for s in self.manifest():
            if s.snapshot_id == sha:
                return s, False
        term = term_of(resource.name)
        rows = read_workbook(content)  # validates the format before archiving
        as_of = date.fromisoformat(resource.last_modified[:10]) if resource.last_modified else now.date()
        rel = f"{term}/{as_of.isoformat()}_{sha[:12]}.xlsx"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        self._write_latest_csv(self.root / term / "latest.csv", rows)
        info = SnapshotInfo(
            snapshot_id=sha,
            term=term,
            resource_id=resource.id,
            resource_name=resource.name,
            last_modified=resource.last_modified or "",
            as_of=as_of,
            fetched_at=now.isoformat(timespec="seconds"),
            path=rel,
            bytes=len(content),
            n_rows=len(rows),
        )
        new_file = not self.manifest_path.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
            if new_file:
                w.writeheader()
            w.writerow({**info.__dict__, "as_of": info.as_of.isoformat()})
        return info, True

    def heartbeat(self, today: date, force: bool = False) -> bool:
        """Update LAST_CHECKED if forced or older than HEARTBEAT_DAYS. Returns True if written."""
        p = self.root / "LAST_CHECKED"
        if not force and p.exists():
            try:
                last = date.fromisoformat(p.read_text().strip())
                if today - last < timedelta(days=HEARTBEAT_DAYS):
                    return False
            except ValueError:
                pass
        self.root.mkdir(parents=True, exist_ok=True)
        p.write_text(today.isoformat() + "\n")
        return True

    @staticmethod
    def _write_latest_csv(path: Path, rows: list[LogRow]) -> None:
        fields = list(COLUMNS.values())
        recs = [{f: _cell_str(r.values.get(f)) for f in fields} for r in rows]
        recs.sort(key=lambda x: (x["item_number"], x["directive"], x["closed_by"], x["assigned_to"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(recs)


@dataclass
class SnapshotRun:
    saved: list[SnapshotInfo] = field(default_factory=list)
    unchanged: list[SnapshotInfo] = field(default_factory=list)
    heartbeat_written: bool = False


def snapshot_report_log(http: PoliteClient, ckan: CkanClient, store: SnapshotStore) -> SnapshotRun:
    """Download each XLSX resource of the log and archive new versions."""
    pkg = ckan.package_show(PACKAGE)
    run = SnapshotRun()
    now = datetime.now(UTC)
    for res in pkg.resources:
        if res.format.upper() != "XLSX":
            continue
        content = http.fetch_cached(res.url, version=res.last_modified).content
        info, is_new = store.save(res, content, now)
        (run.saved if is_new else run.unchanged).append(info)
    run.heartbeat_written = store.heartbeat(now.date(), force=bool(run.saved))
    return run


# --------------------------------------------------------------------------- parsing + load


@dataclass
class _Entry:
    snapshot: SnapshotInfo
    row: LogRow
    item_number: str
    item_id: str | None
    directive_id: str


@dataclass
class IngestStats:
    snapshots: int = 0
    rows: int = 0
    directives: int = 0
    item_id_methods: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    due_rules: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    status_norm: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    with_divisions: int = 0
    with_closed_by_item: int = 0
    unparsed_item_numbers: list[str] = field(default_factory=list)
    orphans_deleted: int = 0


def _snapshot_order(s: SnapshotInfo) -> tuple[int, date, str]:
    return (int(s.term[:4]), s.as_of, s.snapshot_id)


def build_meeting_index(rows: Iterable[LogRow]) -> MeetingIndex:
    idx = MeetingIndex()
    for r in rows:
        ref = normalize_item_id(r.get("item_number"))
        if ref is not None:
            idx.add(ref.item_id)
        for cref in split_item_refs(r.get("closed_by"))[0]:
            idx.add(cref.item_id)
    return idx


def ingest_report_log(conn: Conn, store: SnapshotStore, mapper: DivisionMapper) -> IngestStats:
    snaps = sorted(store.manifest(), key=_snapshot_order)
    parsed: list[tuple[SnapshotInfo, list[LogRow]]] = [
        (s, read_workbook((store.root / s.path).read_bytes())) for s in snaps
    ]
    index = build_meeting_index(r for _, rows in parsed for r in rows)
    stats = IngestStats(snapshots=len(snaps))

    entries: dict[str, list[_Entry]] = defaultdict(list)
    for snap, rows in parsed:
        years = term_years(snap.term)
        for r in rows:
            stats.rows += 1
            raw_item = norm_ws(str(r.get("item_number") or ""))
            ref = normalize_item_id(
                raw_item, index, allowed_years=years, hint=_as_date(r.get("meeting_date"))
            )
            if ref is None:
                stats.unparsed_item_numbers.append(raw_item)
            stats.item_id_methods[ref.method if ref else "unparsed"] += 1
            item_key = ref.item_id if ref else raw_item
            did = directive_key(item_key, str(r.get("directive") or ""))
            entries[did].append(_Entry(snap, r, raw_item, ref.item_id if ref else None, did))

    directive_rows = []
    entry_rows = []
    for did, es in entries.items():
        latest_snap = max((e.snapshot for e in es), key=_snapshot_order)
        latest = [e for e in es if e.snapshot.snapshot_id == latest_snap.snapshot_id]
        first = latest[0]
        r = first.row
        statuses: list[str] = []
        for e in latest:
            s = norm_ws(str(e.row.get("status") or ""))
            if s and s not in statuses:
                statuses.append(s)
        norms = {status_norm(s) for s in statuses} or {"unknown"}
        s_norm = norms.pop() if len(norms) == 1 else "mixed"
        assignees: list[str] = []
        closed_items: list[str] = []
        closed_other: list[str] = []
        for e in latest:
            for a in split_assignees(e.row.get("assigned_to")):
                if a not in assignees:
                    assignees.append(a)
            refs, others = split_item_refs(
                e.row.values.get("closed_by"), index, allowed_years=term_years(e.snapshot.term)
            )
            closed_items += [x.item_id for x in refs if x.item_id not in closed_items]
            closed_other += [o for o in others if o not in closed_other]
        divisions = mapper.map(assignees)
        meeting_date = _as_date(r.get("meeting_date"))
        timing_val = r.values.get("timing")
        timing = parse_timing(timing_val, meeting_date)
        text = norm_ws(str(r.get("directive") or ""))
        no = re.match(r"^(\d+[a-z]?)\.", text)
        seen = sorted({e.snapshot.as_of for e in es})
        directive_rows.append(
            (
                did, first.item_id, first.item_number, latest_snap.term, no.group(1) if no else None,
                norm_ws(str(r.get("req_body") or "")) or None, meeting_date,
                norm_ws(str(r.get("agenda_title") or "")) or None, text,
                norm_ws(str(r.get("report_to") or "")) or None, _cell_str(timing_val) or None,
                timing.due_date, timing.rule, _as_date(r.get("date_for_order")),
                assignees, divisions, " | ".join(statuses) or None, s_norm,
                closed_items, closed_other, seen[0], seen[-1],
            )
        )  # fmt: skip
        stats.directives += 1
        stats.due_rules[timing.rule] += 1
        stats.status_norm[s_norm] += 1
        stats.with_divisions += bool(divisions)
        stats.with_closed_by_item += bool(closed_items)
        for e in es:
            entry_rows.append(
                (
                    e.snapshot.snapshot_id, e.row.row_no, did,
                    norm_ws(str(e.row.get("status") or "")) or None,
                    _cell_str(e.row.values.get("assigned_to")) or None,
                    _cell_str(e.row.values.get("timing")) or None,
                    _cell_str(e.row.values.get("closed_by")) or None,
                )
            )  # fmt: skip

    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO log_snapshot (snapshot_id, term, resource_id, resource_name,
                   last_modified, as_of, path, n_rows)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (snapshot_id) DO UPDATE SET n_rows = EXCLUDED.n_rows,
                   path = EXCLUDED.path""",
            [
                (s.snapshot_id, s.term, s.resource_id, s.resource_name, s.last_modified,
                 s.as_of, s.path, s.n_rows)
                for s in snaps
            ],
        )  # fmt: skip
        cur.executemany(
            """INSERT INTO directive (directive_id, item_id, item_number_raw, term, directive_no,
                   requesting_body, meeting_date, agenda_title, directive_text, report_to,
                   timing_text, due_date, due_rule, clerk_order_date, assigned_to, divisions,
                   log_status, log_status_norm, closed_by_item, closed_by_other,
                   first_seen, last_seen)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s)
               ON CONFLICT (directive_id) DO UPDATE SET
                   item_id = EXCLUDED.item_id, item_number_raw = EXCLUDED.item_number_raw,
                   term = EXCLUDED.term, directive_no = EXCLUDED.directive_no,
                   requesting_body = EXCLUDED.requesting_body,
                   meeting_date = EXCLUDED.meeting_date, agenda_title = EXCLUDED.agenda_title,
                   directive_text = EXCLUDED.directive_text, report_to = EXCLUDED.report_to,
                   timing_text = EXCLUDED.timing_text, due_date = EXCLUDED.due_date,
                   due_rule = EXCLUDED.due_rule, clerk_order_date = EXCLUDED.clerk_order_date,
                   assigned_to = EXCLUDED.assigned_to, divisions = EXCLUDED.divisions,
                   log_status = EXCLUDED.log_status, log_status_norm = EXCLUDED.log_status_norm,
                   closed_by_item = EXCLUDED.closed_by_item,
                   closed_by_other = EXCLUDED.closed_by_other,
                   first_seen = EXCLUDED.first_seen, last_seen = EXCLUDED.last_seen""",
            directive_rows,
        )
        cur.executemany(
            """INSERT INTO log_entry (snapshot_id, row_no, directive_id, status_raw,
                   assigned_to_raw, timing_raw, closed_by_raw)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (snapshot_id, row_no) DO UPDATE SET
                   directive_id = EXCLUDED.directive_id, status_raw = EXCLUDED.status_raw,
                   assigned_to_raw = EXCLUDED.assigned_to_raw,
                   timing_raw = EXCLUDED.timing_raw, closed_by_raw = EXCLUDED.closed_by_raw""",
            entry_rows,
        )
        # Directives no log row points to any more (e.g. an item-number repair changed the
        # key): drop them so re-runs converge to exactly what the snapshots say.
        cur.execute(
            """DELETE FROM directive d WHERE NOT EXISTS
                   (SELECT 1 FROM log_entry e WHERE e.directive_id = d.directive_id)"""
        )
        stats.orphans_deleted = cur.rowcount
    return stats
