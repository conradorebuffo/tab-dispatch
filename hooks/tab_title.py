"""SessionStart hook: title the tab from the role declared in the order ledger.

Reads the hook input from stdin (JSON with `session_id` and `transcript_path`), looks the
role up in the ledger (`tab_roles`, plus the live order THIS session accepted) and appends a
`custom-title` line to the tab's transcript, plus the sidecar the VS Code extension reads.

The same title is also written by `ledger role` and `ledger accept` when they run inside a
Claude Code session, so a tab does not have to restart to get it. See scripts/titles.py for
the limit of the VS Code label.

Fails safe: no role, no ledger, no transcript -> it changes nothing and exits 0. A title
is a convenience; it must never block a session from starting.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from titles import compute_title, native_path, retitle, write_title  # noqa: E402,F401


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
        session_id, transcript = data.get("session_id"), native_path(data.get("transcript_path"))
        if not session_id or not transcript or not Path(transcript).exists():
            return 0
        retitle(session_id, transcript)
    except (ValueError, OSError, sqlite3.Error) as err:
        _log(f"tab_title hook: {type(err).__name__}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
