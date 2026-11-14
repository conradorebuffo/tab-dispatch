"""SessionStart and UserPromptSubmit hook: title the tab from the role in the order ledger.

Reads the hook input from stdin (JSON with `session_id`, `transcript_path` and
`hook_event_name`), looks the role up in the ledger (`tab_roles`) and does two things:

1. Appends a `custom-title` line to the tab's transcript, plus the sidecar the VS Code
   extension reads. `ledger role` and `ledger accept` write the same title.
2. Prints `{"hookSpecificOutput": {"sessionTitle": ...}}`. This is what changes the label of
   an open tab: the extension does not watch the files of step 1. A title is printed once per
   title on `UserPromptSubmit`, and on every `SessionStart`.

A tab whose role was declared after its last message keeps its old label until the next
message. So the Router declares the role of a tab it opens before it sends the first order.

Fails safe: no role, no ledger, no transcript -> it changes nothing and exits 0. A title
is a convenience; it must never block a session from starting.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from titles import announce, compute_title, native_path, retitle, write_title  # noqa: E402,F401


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
        if not session_id:
            return 0
        out = announce(session_id, data.get("hook_event_name"))
        # A prompt with nothing new to announce writes nothing: the title is already on disk.
        if (out or data.get("hook_event_name") != "UserPromptSubmit") and transcript and Path(transcript).exists():
            retitle(session_id, transcript)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as err:
        _log(f"tab_title hook: {type(err).__name__}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
