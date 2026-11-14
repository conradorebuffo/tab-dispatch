"""Tab titles from the order ledger: what a tab should be called, and writing it to disk.

A title is a convenience: every function here fails safe (returns None or False) and never
raises to its caller for a missing transcript or a missing role.

How Claude Code names a tab: it reads the session's transcript (`<session id>.jsonl`, the
last `custom-title` line wins) and a sidecar file `<session id>/custom-title.json` next to it.
Both are written here.

The VS Code extension does not watch these files (measured on 2026-11-12 with the extension
2.1.322): a title written while the tab is open reaches the label only when the extension
lists the sessions again. What changes the label of an open tab is the hook output: a
`SessionStart` or `UserPromptSubmit` hook that returns `sessionTitle` makes Claude Code rename
the session at once (measured on 2026-11-14 with Claude Code 2.1.321). `announce` builds that
output; `hooks/tab_title.py` prints it.
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


_SKIP_WORDS = {"the", "of", "and", "to", "for", "in", "on", "a", "an", "with", "by", "at"}
_MAX_WORDS = 4


def canonical_name(number, name) -> str:
    """`S<number>-<TitleInCamelCase>`: one hyphen, no spaces, at most four meaningful words.

    (42, "State of the art report") -> "S42-StateArtReport". A name that already has the
    `S<number>-` prefix is kept. Accents and punctuation go; a word keeps its own capitals
    (API stays API). With no usable word the name is `S<number>-Session`.
    """
    import unicodedata

    name = (name or "").strip()
    if re.fullmatch(rf"S{number}-\S+", name):
        return name
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    words = [w for w in re.split(r"[^A-Za-z0-9]+", plain) if w]
    useful = [w for w in words if w.lower() not in _SKIP_WORDS] or words
    title = "".join(w[:1].upper() + w[1:] for w in useful[:_MAX_WORDS])
    return f"S{number}-{title or 'Session'}"


def compute_title(role: dict | None, order_id: int | None = None, number: int | None = None) -> str | None:
    """With the session `number`: `S<n>-Router` or `S<n>-<Scope>`, stable for the whole session.

    Without it (0.1.x behavior): `Router`, or `r-<id> <scope>` for a child with a live order.
    """
    if not role:
        return None
    if number is not None:
        return canonical_name(number, "Router" if role["role"] == "coord" else (role.get("scope") or "Child"))
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
    """The title this session should have now: `S<n>-Router` or `S<n>-<Scope>`.

    `<n>` is the session's own number in the ledger (`ledger.session_number`), given the first
    time the session has a role. The title does not change with each order: which order a tab
    holds is in the ledger (`ledger list`), and a stable name is a stable address.
    """
    import ledger  # local import: ledger imports this module too

    role = ledger.get_role(session_id)
    if not role:
        return None
    return compute_title(role, number=ledger.session_number(session_id))


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


ANNOUNCE_EVENTS = ("SessionStart", "UserPromptSubmit")


def _announced_file(session_id: str) -> Path:
    import ledger

    return ledger.data_dir() / "titles" / f"{session_id}.txt"


def announce(session_id: str | None, event: str | None) -> dict | None:
    """The hook output that makes Claude Code show the title of `session_id` on its tab now.

    Returns `{"hookSpecificOutput": {"hookEventName": <event>, "sessionTitle": <title>}}`, or
    None when there is nothing to say: no role, an event that cannot carry a title, or a
    `UserPromptSubmit` for a title this session already announced. `SessionStart` always
    announces, because a tab that starts again shows the default label.
    """
    if not session_id or event not in ANNOUNCE_EVENTS:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
        return None
    title = title_for_session(session_id)
    if not title:
        return None
    marker = _announced_file(session_id)
    if event == "UserPromptSubmit":
        try:
            if marker.read_text(encoding="utf-8") == title:
                return None
        except OSError:
            pass  # never announced: announce now
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(title, encoding="utf-8")
    return {"hookSpecificOutput": {"hookEventName": event, "sessionTitle": title}}
