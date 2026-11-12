"""SessionStart hook: title the tab from the role declared in the order ledger.

Reads the hook input from stdin (JSON with `session_id` and `transcript_path`), looks the
role up in the ledger (`tab_roles`, plus the live order the tab accepted) and appends a
`custom-title` line to the tab's transcript, which is what the VS Code extension shows.

Fails safe: no role, no ledger, no transcript -> it changes nothing and exits 0. A title
is a convenience; it must never block a session from starting.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


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


def _log(msg: str) -> None:
    try:
        import ledger
        log = ledger.data_dir() / "logs" / "hook_errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        print(msg, file=sys.stderr)


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        session_id, transcript = data.get("session_id"), data.get("transcript_path")
        if not session_id or not transcript or not os.path.exists(transcript):
            return 0
        import ledger
        role = ledger.get_role(session_id)
        order_id = None
        if role and role["role"] == "child":
            live = [o for o in ledger.list_orders(live_only=True) if o["state"] == "accepted"
                    and o["tab_title"] in (None, session_id)]
            order_id = live[-1]["id"] if live else None
        title = compute_title(role, order_id)
        if title:
            write_title(transcript, session_id, title)
    except (ValueError, OSError, sqlite3.Error) as err:
        _log(f"tab_title hook: {type(err).__name__}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
