"""Tab titles from the order ledger: what a tab should be called, and writing it to disk.

A title is a convenience: every function here fails safe (returns None or False) and never
raises to its caller for a missing transcript or a missing role.

How Claude Code names a tab: it reads the session's transcript (`<session id>.jsonl`, the
last `custom-title` line wins) and a sidecar file `<session id>/custom-title.json` next to it.
Both are written here.

Known limit (measured on 2026-11-12 with the VS Code extension 2.1.322): the extension does
not watch these files. A title written while the tab is open reaches the tab label only when
the extension lists the sessions again (opening the session history, or reopening the window).
What this module guarantees is the title on disk, per session.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_POSIX_DRIVE = re.compile(r"^/([A-Za-z])/(.*)$")


def native_path(path: str | None) -> str | None:
    """Turn a Git Bash path (`/c/Users/...`) into a Windows one (`C:\\Users\\...`) on Windows.

    The hook input carries the path as the shell gave it. A POSIX path made `os.path.exists`
    fail and the hook changed nothing, with exit 0 and no log.
    """
    if not path:
        return path
    if os.name == "nt":
        m = _POSIX_DRIVE.match(path)
        if m:
            return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
    return path


def projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def find_transcript(session_id: str | None) -> Path | None:
    """The transcript of `session_id` under the Claude Code projects folder, if exactly one."""
    if not session_id or not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
        return None
    root = projects_dir()
    if not root.is_dir():
        return None
    found = list(root.glob(f"*/{session_id}.jsonl"))
    return found[0] if len(found) == 1 else None


def compute_title(role: dict | None, order_id: int | None = None) -> str | None:
    """`Router` for the coordinator, `r-<id> <scope>` for a child with a live order."""
    if not role:
        return None
    if role["role"] == "coord":
        return "Router"
    scope = (role.get("scope") or "child").strip()
    return f"r-{order_id} {scope}" if order_id else f"child {scope}"


def write_title(transcript_path: str, session_id: str, title: str) -> None:
    line = json.dumps({"type": "custom-title", "customTitle": title, "sessionId": session_id},
                      ensure_ascii=False)
    with open(transcript_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    sidecar_dir = os.path.join(os.path.dirname(transcript_path), session_id)
    os.makedirs(sidecar_dir, exist_ok=True)
    tmp = os.path.join(sidecar_dir, "custom-title.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"customTitle": title}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(sidecar_dir, "custom-title.json"))


def title_for_session(session_id: str) -> str | None:
    """The title this session should have now, from its role and the order IT accepted.

    Only an order whose `session_id` is this session counts (or, for orders accepted with
    `--tab <session id>`, its `tab_title`). Before 0.1.1 the newest accepted order with no
    owner was taken, so every child got the title of the last order accepted by anyone.
    """
    import ledger  # local import: ledger imports this module too

    role = ledger.get_role(session_id)
    order_id = None
    if role and role["role"] == "child":
        mine = [o for o in ledger.list_orders(live_only=True)
                if o["state"] == "accepted"
                and (o.get("session_id") == session_id or o.get("tab_title") == session_id)]
        order_id = mine[-1]["id"] if mine else None
    return compute_title(role, order_id)


def retitle(session_id: str | None, transcript_path: str | None = None) -> str | None:
    """Write the current title of `session_id` to disk. Returns the title written, or None."""
    if not session_id:
        return None
    path = native_path(transcript_path) if transcript_path else None
    if not path or not os.path.exists(path):
        found = find_transcript(session_id)
        path = str(found) if found else None
    if not path:
        return None
    title = title_for_session(session_id)
    if title:
        write_title(path, session_id, title)
    return title
