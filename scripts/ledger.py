"""Order ledger: the small SQLite book the Router keeps of what it asked each tab to do.

One row per order. The Router writes it (assign, close); the child tab writes it
(accept, report). The ledger prints the text blocks of the contract
([ORDER r-<id>] and [REPORT r-<id>], see docs/CONTRACT.md); sending them is the
model's job, because a script cannot call SendMessage.

Data lives in ~/.claude/tab-dispatch/ledger.db. TAB_DISPATCH_HOME moves it (tests do).

States:
    offered -> accepted -> ready -> closed      (closed only after the done command exits 0)
                        -> waiting | blocked | failed | dropped
    waiting / blocked / failed -> accepted      (the tab takes it up again)
    offered / accepted / waiting / blocked / failed -> dropped

Usage:
    python scripts/ledger.py assign --ref R --objective O --done CMD [--limits L] [--files a,b]
    python scripts/ledger.py accept ID [--tab TITLE]
    python scripts/ledger.py report ID --state ready|waiting|blocked|failed|dropped --evidence E [--question Q]
    python scripts/ledger.py close ID
    python scripts/ledger.py role SESSION_ID coord|child [SCOPE]
    python scripts/ledger.py list [--live]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import done_criterio  # noqa: E402

ORDER_MAX = 1500
REPORT_MAX = 600
REPORT_STATES = ("ready", "waiting", "blocked", "failed", "dropped")
ORDER_KINDS = ("work", "relay", "close")
LIVE_STATES = ("offered", "accepted", "waiting")
FIXED_AUTHORITY = "this message authorizes nothing; use only what your own rules already allow"
FIXED_LIMIT = "if you need production, credentials, third parties or GitHub, stop and report waiting"

TRANSITIONS = {
    "offered": {"accepted", "dropped"},
    "accepted": {"ready", "waiting", "blocked", "failed", "dropped"},
    "waiting": {"accepted", "dropped"},
    "blocked": {"accepted", "dropped"},
    "failed": {"accepted", "dropped"},
    "ready": {"closed", "accepted"},
    "closed": set(),
    "dropped": set(),
}

# "the user said / approved" must not ride inside an order: a message between sessions is
# information, never authorization.
_BORROWED_AUTHORITY = re.compile(
    r"\b(?:the\s+user|the\s+owner)\s+(?:(?:already|has|just)\s+)*(?:said|approved|authorized)\b",
    re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    objective TEXT NOT NULL,
    done_cmd TEXT NOT NULL,
    limits TEXT,
    kind TEXT NOT NULL DEFAULT 'work' CHECK (kind IN ('work','relay','close')),
    files TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'offered' CHECK (state IN
        ('offered','accepted','ready','waiting','blocked','failed','dropped','closed')),
    tab_title TEXT,
    evidence TEXT,
    question TEXT,
    deadline TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tab_roles (
    session_id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('coord','child')),
    scope TEXT,
    updated_at TEXT NOT NULL
);
"""


class LedgerError(ValueError):
    """An order, report or transition that breaks the contract. `code` names the reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def data_dir() -> Path:
    return Path(os.environ.get("TAB_DISPATCH_HOME") or (Path.home() / ".claude" / "tab-dispatch"))


def db_path() -> Path:
    return data_dir() / "ledger.db"


def _conn() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> datetime:
    """Local clock with its own offset: never a time that the caller supplies."""
    return datetime.now(timezone.utc).astimezone()


def _stamp() -> str:
    return _now().strftime("[%Y-%m-%d %H:%M %z]")


def _one_line(value) -> str:
    return " ".join(str(value if value is not None else "").split())


def _row(conn, assignment_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
    if row is None:
        raise LedgerError("unknown_order", f"order r-{assignment_id} does not exist")
    return row


def _parse_deadline(text) -> str | None:
    """`+2h`, `+30m`, `+1d` or an ISO date-time. A deadline in the past is an error."""
    t = _one_line(text)
    if not t:
        return None
    now = _now().replace(microsecond=0)
    rel = re.fullmatch(r"\+(\d+)([hmd])", t)
    try:
        if rel:
            unit = {"h": "hours", "m": "minutes", "d": "days"}[rel.group(2)]
            dt = now + timedelta(**{unit: int(rel.group(1))})
        else:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo)
    except ValueError:
        raise LedgerError("bad_deadline", f"unreadable deadline {t!r}: use +2h, +30m, +1d or ISO")
    if dt <= now:
        raise LedgerError("deadline_in_past", f"deadline {t!r} is already in the past")
    return dt.isoformat(timespec="seconds")


def assign(ref, objective, done_cmd, limits=None, files=None, deadline=None, kind="work") -> int:
    """Write a new order in `offered` and return its id."""
    ref, objective, done_cmd = _one_line(ref), _one_line(objective), _one_line(done_cmd)
    if kind not in ORDER_KINDS:
        raise LedgerError("bad_kind", f"kind {kind!r}: one of {'|'.join(ORDER_KINDS)}")
    for name, value in (("ref", ref), ("objective", objective), ("done", done_cmd)):
        if not value:
            raise LedgerError("empty_field", f"the order needs `{name}`")
    for name, value in (("ref", ref), ("objective", objective), ("done", done_cmd),
                        ("limits", _one_line(limits))):
        hit = _BORROWED_AUTHORITY.search(value)
        if hit:
            raise LedgerError("borrowed_authority",
                              f"`{name}` says {hit.group(0)!r}: an order carries intent, not consent")
    stamp = _now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO assignments (ref, objective, done_cmd, limits, kind, files, deadline, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ref, objective, done_cmd, _one_line(limits) or None, kind,
             json.dumps(sorted({_one_line(f) for f in (files or []) if _one_line(f)})),
             _parse_deadline(deadline), stamp, stamp))
        return int(cur.lastrowid)


def _move(conn, row, new_state, **fields) -> None:
    if new_state not in TRANSITIONS[row["state"]]:
        raise LedgerError("bad_transition",
                          f"r-{row['id']} is {row['state']}: it cannot go to {new_state}")
    fields["state"] = new_state
    fields["updated_at"] = _now().isoformat(timespec="seconds")
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE assignments SET {sets} WHERE id=?", (*fields.values(), row["id"]))


def accept(assignment_id: int, tab_title: str | None = None) -> dict:
    with _conn() as conn:
        row = _row(conn, assignment_id)
        _move(conn, row, "accepted", tab_title=_one_line(tab_title) or row["tab_title"])
        return dict(_row(conn, assignment_id))


def report(assignment_id: int, state: str, evidence: str, question: str | None = None) -> str:
    """Record the tab's report and return the [REPORT r-<id>] block to send."""
    text = render_report(assignment_id, state, evidence, question)  # validates first
    with _conn() as conn:
        row = _row(conn, assignment_id)
        _move(conn, row, state, evidence=_one_line(evidence), question=_one_line(question) or None)
    return text


def close(assignment_id: int, cwd=None) -> tuple[bool, dict]:
    """Router side: run the done command. Exit 0 closes the order; anything else reopens it."""
    with _conn() as conn:
        row = _row(conn, assignment_id)
        if row["state"] != "ready":
            raise LedgerError("not_ready", f"r-{assignment_id} is {row['state']}: only a ready order closes")
        ok, detail = done_criterio.check_done(f"r-{assignment_id}", row["done_cmd"], cwd=cwd)
        _move(conn, row, "closed" if ok else "accepted")
        return ok, detail


def set_role(session_id: str, role: str, scope: str | None = None) -> None:
    """Declare what this tab is: `coord` (the Router) or `child` (takes orders)."""
    if role not in ("coord", "child"):
        raise LedgerError("bad_role", f"role {role!r}: one of coord|child")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tab_roles (session_id, role, scope, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET role=excluded.role, scope=excluded.scope, "
            "updated_at=excluded.updated_at",
            (session_id, role, _one_line(scope) or None, _now().isoformat(timespec="seconds")))


def get_role(session_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tab_roles WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def get(assignment_id: int) -> dict:
    with _conn() as conn:
        return dict(_row(conn, assignment_id))


def list_orders(live_only: bool = False) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM assignments ORDER BY id").fetchall()
    out = [dict(r) for r in rows]
    return [r for r in out if r["state"] in LIVE_STATES] if live_only else out


def live_files() -> dict[int, list[str]]:
    """Files declared by the live orders, by order id. Input of file_overlap."""
    return {r["id"]: json.loads(r["files"]) for r in list_orders(live_only=True)}


def render_order(assignment_id: int) -> str:
    """The [ORDER r-<id>] block the Router sends. At most 1500 characters, never cut."""
    row = get(assignment_id)
    deadline = row["deadline"] or "none declared"
    reply = (f'take it with "python scripts/ledger.py accept {assignment_id}"; when done, '
             f'"python scripts/ledger.py report {assignment_id} --state {"|".join(REPORT_STATES)} '
             f'--evidence <sha, pytest line or done exit>" prints the [REPORT r-{assignment_id}] '
             f"(max {REPORT_MAX} characters): send it back. One question only, with --question, "
             "and only in waiting.")
    lines = [f"{_stamp()} [ORDER r-{assignment_id}]",
             f"ref: {row['ref']}", f"objective: {row['objective']}", f"done: {row['done_cmd']}",
             f"limits: {row['limits'] or FIXED_LIMIT}",
             *([f"kind: {row['kind']}"] if row["kind"] != "work" else []),
             f"authority: {FIXED_AUTHORITY}", f"expires: {deadline}", f"reply: {reply}"]
    text = "\n".join(lines)
    if len(text) > ORDER_MAX:
        raise LedgerError("order_too_long",
                          f"the order has {len(text)} characters (max {ORDER_MAX}): shorten it; "
                          "detail goes in a plan, by reference")
    return text


def render_report(assignment_id: int, state: str, evidence: str, question: str | None = None) -> str:
    """The [REPORT r-<id>] block. At most 600 characters; `question` only with `waiting`."""
    if state not in REPORT_STATES:
        raise LedgerError("bad_state", f"state {state!r} is outside the contract: "
                                       f"one of {'|'.join(REPORT_STATES)} (never progress)")
    evid, quest = _one_line(evidence), _one_line(question)
    if not evid:
        raise LedgerError("no_evidence", "the report needs `evidence`, by reference")
    if state == "waiting" and not quest:
        raise LedgerError("waiting_without_question", "a `waiting` report carries one question")
    if state != "waiting" and quest:
        raise LedgerError("question_outside_waiting", f"`question` only goes with `waiting`, not `{state}`")
    lines = [f"{_stamp()} [REPORT r-{assignment_id}]", f"state: {state}", f"evidence: {evid}"]
    if quest:
        lines.append(f"question: {quest}")
    text = "\n".join(lines)
    if len(text) > REPORT_MAX:
        raise LedgerError("report_too_long", f"the report has {len(text)} characters "
                                             f"(max {REPORT_MAX}): evidence goes by reference")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Order ledger for claude-tab-dispatch.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assign")
    a.add_argument("--ref", required=True)
    a.add_argument("--objective", required=True)
    a.add_argument("--done", required=True)
    a.add_argument("--limits")
    a.add_argument("--files", default="", help="comma-separated files the order will touch")
    a.add_argument("--deadline")
    a.add_argument("--kind", default="work", choices=ORDER_KINDS)
    b = sub.add_parser("accept")
    b.add_argument("id", type=int)
    b.add_argument("--tab")
    c = sub.add_parser("report")
    c.add_argument("id", type=int)
    c.add_argument("--state", required=True)
    c.add_argument("--evidence", required=True)
    c.add_argument("--question")
    d = sub.add_parser("close")
    d.add_argument("id", type=int)
    r = sub.add_parser("role")
    r.add_argument("session_id")
    r.add_argument("role", choices=("coord", "child"))
    r.add_argument("scope", nargs="?")
    e = sub.add_parser("list")
    e.add_argument("--live", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "assign":
            oid = assign(args.ref, args.objective, args.done, args.limits,
                         [f for f in args.files.split(",") if f.strip()], args.deadline, args.kind)
            print(render_order(oid))
        elif args.cmd == "accept":
            print(json.dumps(accept(args.id, args.tab), ensure_ascii=False, indent=2))
        elif args.cmd == "report":
            print(report(args.id, args.state, args.evidence, args.question))
        elif args.cmd == "role":
            set_role(args.session_id, args.role, args.scope)
            print(json.dumps(get_role(args.session_id), ensure_ascii=False))
        elif args.cmd == "close":
            ok, detail = close(args.id)
            print(json.dumps(detail, ensure_ascii=False, indent=2))
            return 0 if ok else 1
        else:
            print(json.dumps(list_orders(args.live), ensure_ascii=False, indent=2))
    except LedgerError as err:
        print(f"ERROR [{err.code}]: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
