"""Count, per tab, who sent each message the tab received: the human, another tab, or nobody.

Read-only and counts only. It opens the ledger in read-only mode and reads each tab's Claude
Code transcript (`<session id>.jsonl`); it writes nothing. It never stores or prints the text
of a message: what it prints is counts, a marker from a closed list ([ORDER], [REPORT:<state>],
...), the role and scope the ledger holds for the tab, and the name of the sending tab.

Classes (where each one sits in the transcript):

  typed          The human typed it. Two places:
                   - a `type=user` line without `tool_result`, `origin.kind == "human"` (or no
                     origin and `turnOrigin == "human"`), not `isMeta`;
                   - an attachment `type=attachment` with `attachment.type == "queued_command"`
                     and `origin.kind == "human"` (typed while the model was working; it is not a
                     `type=user` line, so counting `type=user` alone undercounts).
  from_tab       A message from another tab (cross-session): `origin.kind == "peer"` and
                 `origin.from` starts with `uds:`. Its marker is read after the time stamp.
  meta           Nobody's message: task notifications, a subagent handing back
                 (`origin.kind == "peer"` with a `from` that is not `uds:`), `isMeta` (skill
                 expansion, system reminders), `turnOrigin == "system"`, local command output and
                 the compaction summary.
  dialogs        Not text: an interrupt (Esc), an answered AskUserQuestion, and a tool call the
                 human rejected in the permission dialog. Approving a permission leaves no line
                 in the transcript, so approvals cannot be counted from it.
  unclassified   A `type=user` line without `tool_result` that no rule matches. It is shown, so a
                 new channel is visible instead of lost.

The tabs counted are the ones with a role in the ledger (`tab_roles`), unless `--session` or
`--transcript` name them. The summary splits the typed messages between the coordinator tab
(the Router) and the rest. The numbers are raw counts of this run; nothing is compared.

Usage:
    python scripts/prompt_count.py [--json]
    python scripts/prompt_count.py --session SESSION_ID [--session SESSION_ID ...]
    python scripts/prompt_count.py --transcript PATH [--transcript PATH ...]
    python scripts/prompt_count.py --since 2026-11-13T18:00 [--until 2026-11-13T20:00]

Exit code: 0 when every tab was counted, 1 when a transcript is missing, 2 for bad arguments
or a missing ledger.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402
import titles  # noqa: E402
from stdio_utf8 import utf8_stdio  # noqa: E402

CLASSES = ("typed", "from_tab", "meta", "dialogs", "unclassified")

# Time stamp at the head of a message between tabs: `[2026-11-12 12:43 -0300]` (any zone label).
STAMP = re.compile(r"^\s*\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?: [^\]\s]{1,8})?\]\s*")
# Closed list of markers: the printed marker always comes from this list, never from the message.
MARKER = re.compile(r"^\[(ORDER|REPORT|ANSWER|NOTICE|NOTE|QUESTION)\b", re.IGNORECASE)
REPORT_STATE = re.compile(r"\[REPORT[^\]]*\][\s\S]{0,40}?state:\s*(ready|waiting|blocked|failed|dropped)",
                          re.IGNORECASE)
CROSS_SESSION_TAG = re.compile(r"(?s)^.*?<cross-session-message[^>]*>\s*")
REJECTED = re.compile(r"(doesn't want to proceed|user rejected|The user doesn't want|tool use was rejected)",
                      re.IGNORECASE)
COMPACTION = "This session is being continued from a previous conversation"
LOCAL_MARKERS = ("<system-reminder", "<local-command", "<command-name", "<command-message")
INTERRUPT = "[Request interrupted by user"


def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _text(blocks) -> str:
    return "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")


def _when(value):
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _marker(text: str) -> str:
    """The marker of a message between tabs, from the closed list, or `free_text`."""
    body = STAMP.sub("", text)
    m = MARKER.match(body)
    if not m and "<cross-session-message" in text:
        m = MARKER.match(STAMP.sub("", CROSS_SESSION_TAG.sub("", text)))
    if not m:
        return "free_text"
    marker = m.group(1).upper()
    if marker == "REPORT":
        state = REPORT_STATE.search(text)
        if state:
            return f"[REPORT:{state.group(1).lower()}]"
    return f"[{marker}]"


def classify(origin, turn_origin, is_meta: bool, text: str) -> tuple[str, str]:
    """(class, subclass) of a message that is not a tool result. The text is only searched for a
    marker; nothing of it is returned."""
    kind = origin.get("kind") if isinstance(origin, dict) else origin
    if kind == "peer":
        sender = str(origin.get("from", "")) if isinstance(origin, dict) else ""
        if sender.startswith("uds:"):
            return "from_tab", _marker(text)
        return "meta", "subagent_handback"
    if kind == "task-notification":
        return "meta", "task_notification"
    if text.lstrip().startswith(INTERRUPT):
        return "dialogs", "interrupt"
    if kind == "human" or (kind is None and turn_origin == "human"):
        if is_meta:
            return "meta", "is_meta"
        if "<command-name>" in text or "<command-message>" in text:
            return "typed", "slash_command"
        return "typed", "text"
    if is_meta:
        return "meta", "is_meta"
    if turn_origin == "system":
        return "meta", "turn_system"
    if text.lstrip().startswith(COMPACTION):
        return "meta", "compaction_summary"
    if text.lstrip().startswith(LOCAL_MARKERS):
        return "meta", "local_marker"
    return "unclassified", "no_rule"


def count_transcript(path, since=None, until=None) -> dict:
    """Count one transcript. `since`/`until` (aware datetimes) keep only what falls inside; the
    rest is counted under `outside_window`. Unreadable lines are counted, never skipped silently."""
    classes, subclasses, senders, dialogs, outside = Counter(), Counter(), Counter(), Counter(), Counter()
    asked = set()
    unreadable = 0
    first = last = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                unreadable += 1
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            kind = entry.get("type")
            ts = _when(entry.get("timestamp"))
            off_window = ts is not None and ((since and ts < since) or (until and ts > until))
            if kind == "assistant":
                for b in _blocks((entry.get("message") or {}).get("content")):
                    if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                        asked.add(b.get("id"))
                continue
            if kind == "attachment":
                att = entry.get("attachment") or {}
                if att.get("type") != "queued_command":
                    continue
                origin = att.get("origin")
                if isinstance(origin, dict) and origin.get("kind") is None and att.get("humanTurn"):
                    origin = {"kind": "human"}
                cls, sub = classify(origin, None, bool(att.get("isMeta")), _text(_blocks(att.get("prompt"))))
            elif kind == "user":
                blocks = _blocks((entry.get("message") or {}).get("content"))
                results = [b for b in blocks if b.get("type") == "tool_result"]
                if results:
                    if off_window:
                        continue
                    for b in results:
                        content = b.get("content")
                        detail = content if isinstance(content, str) else _text(_blocks(content))
                        if b.get("tool_use_id") in asked and not b.get("is_error"):
                            dialogs["ask_user_question_answered"] += 1
                        elif b.get("is_error") and REJECTED.search(detail or ""):
                            dialogs["tool_rejected"] += 1
                    continue
                origin = entry.get("origin")
                cls, sub = classify(origin, entry.get("turnOrigin"), bool(entry.get("isMeta")), _text(blocks))
            else:
                continue
            if off_window:
                outside[cls] += 1
                continue
            if ts is not None:
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last
            if cls == "dialogs":
                dialogs[sub] += 1
                continue
            classes[cls] += 1
            subclasses[f"{cls}:{sub}"] += 1
            if cls == "from_tab" and isinstance(origin, dict):
                senders[str(origin.get("name") or "(no name)")] += 1
    if dialogs:
        classes["dialogs"] = sum(dialogs.values())
    return {
        "counts": {c: classes.get(c, 0) for c in CLASSES},
        "subclasses": dict(sorted(subclasses.items())),
        "dialogs": dict(sorted(dialogs.items())),
        "senders": dict(sorted(senders.items())),
        "outside_window": dict(sorted(outside.items())),
        "unreadable_lines": unreadable,
        "first_at": first.isoformat(timespec="seconds") if first else None,
        "last_at": last.isoformat(timespec="seconds") if last else None,
    }


def ledger_tabs(db=None) -> list[dict]:
    """Tabs with a role in the ledger, read in read-only mode. Raises FileNotFoundError without one."""
    path = Path(db) if db else ledger.db_path()
    if not path.is_file():
        raise FileNotFoundError(f"no ledger at {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = [dict(r) for r in conn.execute("SELECT session_id, role, scope FROM tab_roles ORDER BY updated_at")]
        if "sessions" in tables:
            numbers = dict(conn.execute("SELECT session_id, n FROM sessions").fetchall())
            for r in rows:
                r["number"] = numbers.get(r["session_id"])
    finally:
        conn.close()
    return rows


def count_tabs(tabs: list[dict], since=None, until=None) -> dict:
    """Count every tab and add the summary of typed messages by role."""
    out = []
    for tab in tabs:
        path = tab.get("transcript") or titles.find_transcript(tab.get("session_id"))
        row = {k: tab.get(k) for k in ("session_id", "role", "scope", "number") if tab.get(k) is not None}
        if not path or not Path(path).is_file():
            row["error"] = "transcript not found"
            out.append(row)
            continue
        row["transcript"] = Path(path).name
        row.update(count_transcript(path, since, until))
        out.append(row)
    typed = [r["counts"]["typed"] for r in out if "counts" in r]
    in_coord = sum(r["counts"]["typed"] for r in out if "counts" in r and r.get("role") == "coord")
    return {
        "tabs": out,
        "summary": {
            "tabs_counted": len(typed),
            "typed_total": sum(typed),
            "typed_in_coord": in_coord,
            "typed_in_other_tabs": sum(typed) - in_coord,
        },
    }


def _label(row: dict) -> str:
    canonical = getattr(titles, "canonical_name", None)  # 0.2 names; a 0.1 titles.py lacks it
    if row.get("number") is not None and canonical:
        name = canonical(row["number"], "Router" if row.get("role") == "coord" else row.get("scope"))
    elif row.get("session_id"):
        name = str(row["session_id"])[:8]
    else:
        name = row.get("transcript") or "?"
    return f"{name} ({row['role']})" if row.get("role") else name


def render(result: dict) -> str:
    head = f"{'tab':<36} " + " ".join(f"{c:>12}" for c in CLASSES)
    lines = [head, "-" * len(head)]
    for row in result["tabs"]:
        if "error" in row:
            lines.append(f"{_label(row)[:36]:<36} ERROR: {row['error']}")
            continue
        lines.append(f"{_label(row)[:36]:<36} " + " ".join(f"{row['counts'][c]:>12}" for c in CLASSES))
        if row["unreadable_lines"]:
            lines.append(f"    {row['unreadable_lines']} unreadable lines")
    s = result["summary"]
    lines.append("")
    lines.append(f"Typed by the human: {s['typed_total']} messages; {s['typed_in_coord']} in the coordinator "
                 f"tab, {s['typed_in_other_tabs']} in other tabs ({s['tabs_counted']} tabs counted).")
    return "\n".join(lines)


def _moment(text: str):
    ts = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.astimezone()


def main(argv=None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", action="append", default=[], help="session id to count (repeatable)")
    ap.add_argument("--transcript", action="append", default=[], help="transcript path to count (repeatable)")
    ap.add_argument("--since", help="count only from this moment on (ISO; no zone = local time)")
    ap.add_argument("--until", help="count only up to this moment (ISO; no zone = local time)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)
    try:
        since = _moment(args.since) if args.since else None
        until = _moment(args.until) if args.until else None
    except ValueError as err:
        print(f"prompt_count: bad --since/--until: {err}", file=sys.stderr)
        return 2
    try:
        roles = {t["session_id"]: t for t in ledger_tabs()}
    except FileNotFoundError as err:
        if not (args.session or args.transcript):
            print(f"prompt_count: {err}; pass --session or --transcript", file=sys.stderr)
            return 2
        roles = {}
    except sqlite3.Error as err:
        print(f"prompt_count: cannot read the ledger: {err}", file=sys.stderr)
        return 2
    if args.session or args.transcript:
        tabs = [dict(roles.get(s, {}), session_id=s) for s in args.session]
        for p in args.transcript:
            sid = Path(p).stem
            tabs.append(dict(roles.get(sid, {}), session_id=sid, transcript=titles.native_path(p)))
    else:
        tabs = list(roles.values())
    result = count_tabs(tabs, since, until)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 1 if any("error" in r for r in result["tabs"]) else 0


if __name__ == "__main__":
    sys.exit(main())
