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
    python scripts/ledger.py accept ID [--tab TITLE] [--session SESSION_ID]
    python scripts/ledger.py report ID --state ready|waiting|blocked|failed|dropped --evidence E [--question Q]
    python scripts/ledger.py close ID
    python scripts/ledger.py role SESSION_ID coord|child [SCOPE]
    python scripts/ledger.py list [--live]
    python scripts/ledger.py attention [--json|--all]
    python scripts/ledger.py plan register ID --path plans/NNN_slug.md | approve ID | check ID [--step N]
    python scripts/ledger.py thread park THREAD --outcome continue|pause|done --context C --done D --next N
    python scripts/ledger.py thread resume THREAD | list [--status paused]
    python scripts/ledger.py lesson add TEXT [--order ID]
    python scripts/ledger.py session report --out FILE [--since ISO]

`assign` also takes --kind work|plan|relay|close, --model and --effort.
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
ORDER_KINDS = ("work", "plan", "relay", "close")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
THREAD_OUTCOMES = {"continue": "active", "pause": "paused", "done": "done"}  # outcome -> thread status
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
    kind TEXT NOT NULL DEFAULT 'work' CHECK (kind IN ('work','plan','relay','close')),
    files TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'offered' CHECK (state IN
        ('offered','accepted','ready','waiting','blocked','failed','dropped','closed')),
    tab_title TEXT,
    session_id TEXT,
    evidence TEXT,
    question TEXT,
    deadline TEXT,
    model TEXT,
    effort TEXT,
    plan_path TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tab_roles (
    session_id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('coord','child')),
    scope TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    n INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    exit_code INTEGER,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL CHECK (status IN ('active','paused','done')),
    path TEXT NOT NULL,
    next_step TEXT,
    last_session TEXT,
    updated_at TEXT NOT NULL
);
"""
_ASSIGNMENTS_DDL = _SCHEMA[_SCHEMA.index("CREATE TABLE IF NOT EXISTS assignments"):_SCHEMA.index("CREATE TABLE IF NOT EXISTS tab_roles")]
_NEW_COLUMNS = ("session_id", "model", "effort", "plan_path", "approved_at")
_THREAD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,48}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}")
_PLAN_NAME = re.compile(r"\d{3}_[a-z0-9][a-z0-9_-]*\.md")
_PLAN_STEP = re.compile(r"^- \[( |x)\] Step (\d+): (.+?)\s+done: `([^`]+)`\s*$", re.MULTILINE)


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
    cols = [r[1] for r in conn.execute("PRAGMA table_info(assignments)")]
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='assignments'").fetchone()[0]
    if "'plan'" not in ddl or any(c not in cols for c in _NEW_COLUMNS):
        # A ledger written by 0.1.x: its CHECK on `kind` rejects 'plan' and SQLite cannot alter a
        # CHECK, so the table is rebuilt with the same rows and ids.
        keep = ", ".join(cols)
        conn.executescript(
            "BEGIN; ALTER TABLE assignments RENAME TO assignments_old; " + _ASSIGNMENTS_DDL
            + f"INSERT INTO assignments ({keep}) SELECT {keep} FROM assignments_old; "
            "DROP TABLE assignments_old; COMMIT;")
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


def assign(ref, objective, done_cmd, limits=None, files=None, deadline=None, kind="work",
           model=None, effort=None) -> int:
    """Write a new order in `offered` and return its id.

    `model` and `effort` say what the Router wants the order to run on. They are a declared
    requirement: nothing here can switch the model of another tab.
    """
    ref, objective, done_cmd = _one_line(ref), _one_line(objective), _one_line(done_cmd)
    model, effort = _one_line(model) or None, _one_line(effort) or None
    if kind not in ORDER_KINDS:
        raise LedgerError("bad_kind", f"kind {kind!r}: one of {'|'.join(ORDER_KINDS)}")
    if model and not _MODEL.fullmatch(model):
        raise LedgerError("bad_model", f"model {model!r}: a model alias or id, such as opus or sonnet")
    if effort and effort not in EFFORTS:
        raise LedgerError("bad_effort", f"effort {effort!r}: one of {'|'.join(EFFORTS)}")
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
            "model, effort, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ref, objective, done_cmd, _one_line(limits) or None, kind,
             json.dumps(sorted({_one_line(f) for f in (files or []) if _one_line(f)})),
             _parse_deadline(deadline), model, effort, stamp, stamp))
        return int(cur.lastrowid)


def _move(conn, row, new_state, **fields) -> None:
    if new_state not in TRANSITIONS[row["state"]]:
        raise LedgerError("bad_transition",
                          f"r-{row['id']} is {row['state']}: it cannot go to {new_state}")
    fields["state"] = new_state
    fields["updated_at"] = _now().isoformat(timespec="seconds")
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE assignments SET {sets} WHERE id=?", (*fields.values(), row["id"]))


def current_session_id() -> str | None:
    """The Claude Code session this process runs in, if any (set by Claude Code itself)."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _retitle(session_id: str | None) -> str | None:
    """Write the tab title of `session_id` now. A title never makes a ledger call fail."""
    if not session_id:
        return None
    try:
        import titles
        return titles.retitle(session_id)
    except (OSError, ValueError, sqlite3.Error) as err:
        log = data_dir() / "logs" / "hook_errors.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"retitle {session_id}: {type(err).__name__}: {err}\n")
        except OSError:
            print(f"retitle {session_id}: {type(err).__name__}: {err}", file=sys.stderr)
        return None


def accept(assignment_id: int, tab_title: str | None = None, session_id: str | None = None) -> dict:
    """Take an order. The accepting session is recorded, so the title names this order only."""
    session_id = _one_line(session_id) or current_session_id()
    with _conn() as conn:
        row = _row(conn, assignment_id)
        _move(conn, row, "accepted", tab_title=_one_line(tab_title) or row["tab_title"],
              session_id=session_id or row["session_id"])
        out = dict(_row(conn, assignment_id))
    _retitle(session_id)
    return out


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
        _record_check(conn, assignment_id, "done", detail)
        _move(conn, row, "closed" if ok else "accepted")
        return ok, detail


def _record_check(conn, assignment_id: int, label: str, detail: dict) -> None:
    """Keep the exit code of every done check: the session report counts them."""
    conn.execute("INSERT INTO checks (assignment_id, label, exit_code, at) VALUES (?,?,?,?)",
                 (assignment_id, label, detail.get("exit_code"), _now().isoformat(timespec="seconds")))


def session_number(session_id: str) -> int:
    """The plugin's own running number of a session: 1 for the first tab that got a role.

    Claude Code gives a session a UUID, not a number, so the `<n>` of `S<n>-<Title>` is counted
    here, in the ledger, the first time a session is seen.
    """
    with _conn() as conn:
        row = conn.execute("SELECT n FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            # Not INSERT OR IGNORE: an ignored insert still takes a number, and the names skip
            # (S1, S2, S3, S7, S14 in a run of five tabs).
            n = conn.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM sessions").fetchone()[0]
            conn.execute("INSERT INTO sessions (n, session_id, created_at) VALUES (?,?,?)",
                         (n, session_id, _now().isoformat(timespec="seconds")))
            return int(n)
        return int(row[0])


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
    session_number(session_id)  # the number follows the order in which roles are declared
    _retitle(session_id)


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
             *([f"model: {row['model']}"] if row["model"] else []),
             *([f"effort: {row['effort']}"] if row["effort"] else []),
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


# ---------------------------------------------------------------- plan orders
def plan_steps(path) -> list[dict]:
    """The steps of a plan file: `- [ ] Step N: what  done: `command``, one line each."""
    text = Path(path).read_text(encoding="utf-8")
    return [{"n": int(n), "ticked": mark == "x", "what": what.strip(), "done": cmd.strip()}
            for mark, n, what, cmd in _PLAN_STEP.findall(text)]


def plan_register(assignment_id: int, path) -> dict:
    """Child side: record the plan file written for a `plan` order. It then waits for approval."""
    p = Path(path).resolve()
    with _conn() as conn:
        row = _row(conn, assignment_id)
        if row["kind"] != "plan":
            raise LedgerError("not_a_plan_order", f"r-{assignment_id} is a {row['kind']} order")
        if row["state"] != "accepted":
            raise LedgerError("bad_transition", f"r-{assignment_id} is {row['state']}: accept it first")
        if p.parent.name != "plans" or not _PLAN_NAME.fullmatch(p.name):
            raise LedgerError("bad_plan_path", f"{path}: a plan file is plans/NNN_slug.md")
        if not p.is_file():
            raise LedgerError("no_plan_file", f"{p} does not exist")
        steps = plan_steps(p)
        if not steps:
            raise LedgerError("plan_without_steps",
                              "the plan needs steps written as: - [ ] Step 1: what  done: `command`")
        conn.execute("UPDATE assignments SET plan_path=?, approved_at=NULL, updated_at=? WHERE id=?",
                     (str(p), _now().isoformat(timespec="seconds"), assignment_id))
    return {"id": assignment_id, "plan_path": str(p), "steps": len(steps)}


def plan_approve(assignment_id: int) -> dict:
    """Router side, after the human said yes: the child may now execute the plan."""
    with _conn() as conn:
        row = _row(conn, assignment_id)
        if not row["plan_path"]:
            raise LedgerError("no_plan_registered", f"r-{assignment_id} has no plan file registered")
        stamp = _now().isoformat(timespec="seconds")
        conn.execute("UPDATE assignments SET approved_at=?, updated_at=? WHERE id=?",
                     (stamp, stamp, assignment_id))
    return {"id": assignment_id, "plan_path": row["plan_path"], "approved_at": stamp}


def plan_check(assignment_id: int, step: int | None = None) -> tuple[bool, list[dict]]:
    """Run the done command of every step (or one). All exit 0 = the plan is done."""
    row = get(assignment_id)
    if not row["plan_path"]:
        raise LedgerError("no_plan_registered", f"r-{assignment_id} has no plan file registered")
    if not row["approved_at"]:
        raise LedgerError("plan_not_approved", f"r-{assignment_id}: the plan is not approved yet")
    root = Path(row["plan_path"]).parent.parent
    steps = [s for s in plan_steps(row["plan_path"]) if step is None or s["n"] == step]
    if not steps:
        raise LedgerError("unknown_step", f"r-{assignment_id} has no step {step}")
    results = []
    with _conn() as conn:
        for s in steps:
            ok, detail = done_criterio.check_done(f"r-{assignment_id} step {s['n']}", s["done"], cwd=str(root))
            _record_check(conn, assignment_id, f"step {s['n']}", detail)
            results.append({"step": s["n"], "ok": ok, "exit_code": detail.get("exit_code"), "done": s["done"]})
    return all(r["ok"] for r in results), results


# ---------------------------------------------------------------- threads
def _thread_row(conn, thread_id: str):
    return conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()


def thread_park(thread_id, outcome, context, done, next_step, title=None, session_id=None, root=None) -> dict:
    """Leave a piece of work so that another session can take it up: `threads/<id>.md`.

    `continue` hands it to a fresh tab now, `pause` leaves it for another day (the attention
    queue lists it), `done` closes it.
    """
    thread_id = _one_line(thread_id)
    if not _THREAD_ID.fullmatch(thread_id):
        raise LedgerError("bad_thread_id", f"thread id {thread_id!r}: lower-case letters, digits and hyphens")
    if outcome not in THREAD_OUTCOMES:
        raise LedgerError("bad_outcome", f"outcome {outcome!r}: one of {'|'.join(THREAD_OUTCOMES)}")
    context, done, next_step = _one_line(context), _one_line(done), _one_line(next_step)
    if not context or (outcome != "done" and not next_step):
        raise LedgerError("empty_field", "a thread needs `context`, and `next` unless it is done")
    session_id = _one_line(session_id) or current_session_id()
    with _conn() as conn:
        old = _thread_row(conn, thread_id)
        path = Path(old["path"]) if old else (Path(root) if root else Path.cwd()) / "threads" / f"{thread_id}.md"
        title = _one_line(title) or (old["title"] if old else None) or thread_id
        log = []
        if path.is_file():
            log = [ln for ln in path.read_text(encoding="utf-8").split("## Log\n", 1)[-1].splitlines()
                   if ln.startswith("- ")]
        log.append(f"- {_stamp()} {outcome}" + (f" by session {session_id}" if session_id else ""))
        status = THREAD_OUTCOMES[outcome]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Thread {thread_id}: {title}\n\nstatus: {status}\n\n## Context\n\n{context}\n\n"
            f"## Done\n\n{done or 'nothing yet'}\n\n## Next step\n\n{next_step or 'none: the thread is done'}\n\n"
            "## Log\n\n" + "\n".join(log) + "\n", encoding="utf-8")
        conn.execute(
            "INSERT INTO threads (id, title, status, path, next_step, last_session, updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "status=excluded.status, next_step=excluded.next_step, last_session=excluded.last_session, "
            "updated_at=excluded.updated_at",
            (thread_id, title, status, str(path.resolve()), next_step or None, session_id,
             _now().isoformat(timespec="seconds")))
        return dict(_thread_row(conn, thread_id))


def thread_resume(thread_id: str, session_id: str | None = None) -> dict:
    """A new session takes a parked thread: it becomes active and its file is returned."""
    session_id = _one_line(session_id) or current_session_id()
    with _conn() as conn:
        row = _thread_row(conn, _one_line(thread_id))
        if row is None:
            raise LedgerError("unknown_thread", f"thread {thread_id!r} does not exist")
        if row["status"] == "done":
            raise LedgerError("thread_done", f"thread {thread_id} is done: start a new one")
        path = Path(row["path"])
        if not path.is_file():
            raise LedgerError("no_thread_file", f"{path} does not exist")
        text = path.read_text(encoding="utf-8").replace(f"status: {row['status']}", "status: active", 1)
        text = text.rstrip("\n") + f"\n- {_stamp()} resumed" + (f" by session {session_id}" if session_id else "") + "\n"
        path.write_text(text, encoding="utf-8")
        conn.execute("UPDATE threads SET status='active', last_session=?, updated_at=? WHERE id=?",
                     (session_id, _now().isoformat(timespec="seconds"), row["id"]))
        return {**dict(_thread_row(conn, row["id"])), "text": text}


def list_threads(status: str | None = None) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM threads ORDER BY updated_at").fetchall()
    return [dict(r) for r in rows if status is None or r["status"] == status]


# ---------------------------------------------------------------- attention, lessons, report
def attention() -> list[dict]:
    """What waits for the human, oldest first. Derived from the ledger each time: no state of its own."""
    items = []
    for o in list_orders():
        base = {"id": f"r-{o['id']}", "ref": o["ref"], "since": o["updated_at"]}
        if o["kind"] == "plan" and o["plan_path"] and not o["approved_at"] and o["state"] in ("accepted", "waiting"):
            items.append({**base, "type": "plan_to_approve", "what": f"plan {o['plan_path']}",
                          "resolve": f"python scripts/ledger.py plan approve {o['id']}"})
        elif o["state"] == "waiting":
            items.append({**base, "type": "question", "what": o["question"] or o["evidence"] or "",
                          "resolve": f"answer the tab; it takes the order again with: python scripts/ledger.py accept {o['id']}"})
        elif o["state"] in ("blocked", "failed"):
            items.append({**base, "type": o["state"], "what": o["evidence"] or "",
                          "resolve": f"decide: retry (accept {o['id']}) or drop (report {o['id']} --state dropped)"})
    for t in list_threads("paused"):
        items.append({"id": t["id"], "ref": t["title"], "since": t["updated_at"], "type": "paused_thread",
                      "what": t["next_step"] or "", "resolve": f"python scripts/ledger.py thread resume {t['id']}"})
    return sorted(items, key=lambda i: i["since"])


def lessons_path() -> Path:
    return data_dir() / "lessons.md"


def lesson_add(text: str, order_id: int | None = None) -> Path:
    """Append one lesson to lessons.md, stamped. The session report quotes them."""
    text = _one_line(text)
    if not text:
        raise LedgerError("empty_field", "a lesson needs text")
    path = lessons_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {_stamp()} " + (f"(r-{order_id}) " if order_id else "") + text + "\n")
    return path


def _count(values) -> str:
    seen: dict[str, int] = {}
    for v in values:
        seen[v] = seen.get(v, 0) + 1
    return ", ".join(f"{k} {n}" for k, n in sorted(seen.items())) or "none"


def session_report(out, since: str | None = None) -> dict:
    """Write the figures of the ledger to `out`: orders, exit codes, times, lessons."""
    orders = [o for o in list_orders() if not since or o["created_at"] >= since]
    ids = {o["id"] for o in orders}
    with _conn() as conn:
        checks = [dict(r) for r in conn.execute("SELECT * FROM checks ORDER BY id") if r["assignment_id"] in ids]
    closed = [o for o in orders if o["state"] == "closed"]
    minutes = sorted((datetime.fromisoformat(o["updated_at"]) - datetime.fromisoformat(o["created_at"])).total_seconds() / 60
                     for o in closed)
    zero = sum(1 for c in checks if c["exit_code"] == 0)
    lessons = []
    if lessons_path().is_file():
        lessons = [ln for ln in lessons_path().read_text(encoding="utf-8").splitlines()
                   if ln.startswith("- ") and (not since or ln[3:13] >= since[:10])]
    lines = [
        "# tab-dispatch session report", "",
        f"generated: {_stamp()}  |  orders since: {since or 'the first one'}", "",
        "## Orders", "",
        f"- total: {len(orders)}",
        f"- by state: {_count(o['state'] for o in orders)}",
        f"- by kind: {_count(o['kind'] for o in orders)}",
        f"- by model and effort: {_count((o['model'] or 'not declared') + '/' + (o['effort'] or 'not declared') for o in orders)}",
        "", "## Exit codes", "",
        f"- orders closed by exit 0 of their done command: {len(closed)} of {len(orders)}",
        f"- done checks recorded: {len(checks)} (exit 0: {zero}, any other exit: {len(checks) - zero})",
        "", "## Times", "",
        (f"- from order to close, {len(minutes)} closed orders: median {minutes[len(minutes) // 2]:.1f} min, "
         f"longest {minutes[-1]:.1f} min" if minutes else "- no closed order yet"),
        "", "## Waiting for the human", "",
        f"- items in the attention queue now: {len(attention())}",
        "", "## Lessons", "", *(lessons or ["- none recorded"]), ""]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return {"out": str(out), "orders": len(orders), "closed": len(closed), "checks": len(checks),
            "lessons": len(lessons)}


def main(argv=None) -> int:
    from stdio_utf8 import utf8_stdio
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Order ledger for tab-dispatch.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assign")
    a.add_argument("--ref", required=True)
    a.add_argument("--objective", required=True)
    a.add_argument("--done", required=True)
    a.add_argument("--limits")
    a.add_argument("--files", default="", help="comma-separated files the order will touch")
    a.add_argument("--deadline")
    a.add_argument("--kind", default="work", choices=ORDER_KINDS)
    a.add_argument("--model", help="model the order should run on, such as opus, sonnet or haiku")
    a.add_argument("--effort", choices=EFFORTS, help="effort level the order should run at")
    b = sub.add_parser("accept")
    b.add_argument("id", type=int)
    b.add_argument("--tab")
    b.add_argument("--session", help="accepting session id (default: $CLAUDE_CODE_SESSION_ID)")
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
    at = sub.add_parser("attention", help="what waits for the human, one item at a time")
    at.add_argument("--json", action="store_true", help="every item, as JSON")
    at.add_argument("--all", action="store_true", help="every item, as text")
    pl = sub.add_parser("plan").add_subparsers(dest="plan_cmd", required=True)
    p1 = pl.add_parser("register")
    p1.add_argument("id", type=int)
    p1.add_argument("--path", required=True, help="plans/NNN_slug.md")
    pl.add_parser("approve").add_argument("id", type=int)
    p3 = pl.add_parser("check")
    p3.add_argument("id", type=int)
    p3.add_argument("--step", type=int)
    th = sub.add_parser("thread").add_subparsers(dest="thread_cmd", required=True)
    t1 = th.add_parser("park")
    t1.add_argument("thread")
    t1.add_argument("--outcome", required=True, choices=tuple(THREAD_OUTCOMES))
    t1.add_argument("--context", required=True)
    t1.add_argument("--done", default="")
    t1.add_argument("--next", default="")
    t1.add_argument("--title")
    t1.add_argument("--session")
    t2 = th.add_parser("resume")
    t2.add_argument("thread")
    t2.add_argument("--session")
    th.add_parser("list").add_argument("--status", choices=tuple(THREAD_OUTCOMES.values()))
    l1 = sub.add_parser("lesson").add_subparsers(dest="lesson_cmd", required=True).add_parser("add")
    l1.add_argument("text")
    l1.add_argument("--order", type=int)
    s1 = sub.add_parser("session").add_subparsers(dest="session_cmd", required=True).add_parser("report")
    s1.add_argument("--out", required=True)
    s1.add_argument("--since", help="only orders created at or after this ISO date-time")
    args = ap.parse_args(argv)

    def show(obj):
        print(json.dumps(obj, ensure_ascii=False, indent=2))

    try:
        if args.cmd == "assign":
            oid = assign(args.ref, args.objective, args.done, args.limits,
                         [f for f in args.files.split(",") if f.strip()], args.deadline, args.kind,
                         args.model, args.effort)
            print(render_order(oid))
        elif args.cmd == "attention":
            items = attention()
            if args.json:
                show({"count": len(items), "items": items})
            elif not items:
                print("Nothing waits for you.")
            else:
                for n, it in enumerate(items if args.all else items[:1], 1):
                    print(f"[{n} of {len(items)}] {it['type']} {it['id']} ({it['ref']})\n"
                          f"  what: {it['what']}\n  resolve: {it['resolve']}")
        elif args.cmd == "plan":
            if args.plan_cmd == "register":
                show(plan_register(args.id, args.path))
            elif args.plan_cmd == "approve":
                show(plan_approve(args.id))
            else:
                ok, results = plan_check(args.id, args.step)
                show(results)
                return 0 if ok else 1
        elif args.cmd == "thread":
            if args.thread_cmd == "park":
                show(thread_park(args.thread, args.outcome, args.context, args.done, args.next,
                                 args.title, args.session))
            elif args.thread_cmd == "resume":
                print(thread_resume(args.thread, args.session)["text"])
            else:
                show(list_threads(args.status))
        elif args.cmd == "lesson":
            print(lesson_add(args.text, args.order))
        elif args.cmd == "session":
            show(session_report(args.out, args.since))
        elif args.cmd == "accept":
            print(json.dumps(accept(args.id, args.tab, args.session), ensure_ascii=False, indent=2))
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
