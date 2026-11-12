# -*- coding: utf-8 -*-
"""tab_launch.py -- Opens a new Claude Code tab in VS Code through a deep link and waits
until another session can write to it (session relay).

Flow:
    1. Snapshot of the live ``sessionId`` values (``claude agents --json``).
    2. Launches ``Code.exe --open-url -- vscode://anthropic.claude-code/open`` with the
       child process environment WITHOUT ``ELECTRON_RUN_AS_NODE``.
    3. Polls ``claude agents --json`` until it sees an ``interactive`` session that was not
       in the snapshot, or until ``--timeout``.
    4. Prints the result (``--json`` to consume it from the Router).

Usage:
    python scripts/tab_launch.py --thread <thread_id> --json
    python scripts/tab_launch.py --dry-run --json

Exit codes:
    0  tab detected
    2  timeout: no new session appeared (almost always, the VS Code dialog
       is still unconfirmed)
    1  error: not Windows, Code.exe or ``claude`` not found, Code.exe returned a
       non-zero exit, two new sessions at once, invalid arguments, etc.
       (argparse uses 2 for usage errors; here it is forced to 1 so it is not confused
       with the timeout)

FACTS MEASURED in the earlier tests (2026-11-11):

* F0-i. The Bash/Python of a Claude Code tab inherits ``ELECTRON_RUN_AS_NODE``.
  With that variable ``Code.exe`` runs as Node and rejects the protocol handler:
  ``bad option: --open-url`` (exit 9). ``os.startfile`` triggers the same handler with
  the same environment and fails with no visible error. That is why ``Code.exe`` is run
  by hand here, with the variable removed from the child environment (the parent's is
  not touched).
* F0-j. VS Code shows the modal dialog "Allow 'Claude Code for VS Code' extension to
  open this URI?" on every launch, unless the user ticked "Do not ask me
  again for this extension". While nobody clicks "Open" the tab does not exist: that is
  exit 2. If the dialog is confirmed after the timeout the tab opens anyway, so
  before relaunching it is worth looking at ``claude agents --json``.
* F0-g. The tab opened by URI shows up in ``claude agents --json`` as an
  ``interactive`` / ``idle`` session with a new ``sessionId`` and a native name like
  ``myproject-b3`` BEFORE receiving any prompt. That is why the default is to open WITHOUT
  a preloaded prompt: the Router sends it the first order by message. ``--thread`` is only
  recorded and returned; it preloads nothing. ``--prompt`` exists for the manual case
  (the text is typed into the box, unsent).

The URI also accepts a ``session`` parameter (resume an existing session). This
script does not expose it: the relay always opens a new tab.

Every launch leaves an append-only trace in ``~/.claude/tab-dispatch/logs/tab_launch.log`` (one JSON
line per event, with the ART time taken from the clock): ``launched`` as soon as ``Code.exe``
finishes, and ``result`` at the end. Two lines on purpose: if the process dies
during the wait, there is a record that a dialog is open on screen.
``--dry-run`` neither launches nor writes the log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

ART = timezone(timedelta(hours=-3))

URI_BASE = "vscode://anthropic.claude-code/open"

#: Variables removed from the child process environment (F0-i). Only what was measured.
ENV_TO_REMOVE = ("ELECTRON_RUN_AS_NODE",)

#: Command of the ``vscode`` protocol (HKCR is the merged view of HKCU and HKLM).
REG_SUBKEY = r"vscode\shell\open\command"

#: Last resort if there is no ``--code-exe`` and no registry: the per-user install.
CODE_EXE_LAST_RESORT = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
    "Programs", "Microsoft VS Code", "Code.exe")

TIMEOUT_DEFAULT_S = 90.0
POLL_S = 2.0
#: With a VS Code instance already open, ``Code.exe --open-url`` forwards the URI and exits
#: right away. If it does not exit within this window it is NOT killed: it may be the main instance.
CODE_EXE_WAIT_S = 20.0
AGENTS_TIMEOUT_S = 30.0

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2

#: Plugin data folder (the same one the order ledger uses). TAB_DISPATCH_HOME changes it.
DATA_DIR = Path(os.environ.get("TAB_DISPATCH_HOME") or (Path.home() / ".claude" / "tab-dispatch"))
LOG_PATH = DATA_DIR / "logs" / "tab_launch.log"


class TabLaunchError(Exception):
    """Failure that ends the run with exit 1 and a readable message."""


def _warn(msg):
    print(f"[tab_launch] {msg}", file=sys.stderr)


def is_windows():
    return sys.platform == "win32"


def now_art():
    """ART time read from the machine clock (never estimated by a model: F0-b)."""
    return datetime.now(ART).strftime("%Y-%m-%d %H:%M:%S ART")


# ---------------------------------------------------------------- URI and environment

def build_uri(prompt=None):
    """Deep link URI. Without a prompt (default) the tab opens with an empty box."""
    if prompt is None or not str(prompt).strip():
        return URI_BASE
    # safe="" also encodes '/', ':' and '&'; the space goes as %20, never '+'.
    return f"{URI_BASE}?prompt={quote(str(prompt), safe='')}"


def child_env(base=None):
    """Copy of the environment without the ``ENV_TO_REMOVE`` variables.

    Compares in upper case: on Windows variable names are case-insensitive.
    ALWAYS returns a new dict; the parent environment is left intact.
    """
    source = os.environ if base is None else base
    remove = {v.upper() for v in ENV_TO_REMOVE}
    return {k: v for k, v in source.items() if k.upper() not in remove}


def present_variables(base=None):
    """Which of ``ENV_TO_REMOVE`` are in the environment today. Names only, never values."""
    source = os.environ if base is None else base
    present = {k.upper() for k in source}
    return [v for v in ENV_TO_REMOVE if v.upper() in present]


# ---------------------------------------------------------------- Code.exe

def exe_from_command(command):
    """Extracts the executable from a registry protocol command.

    ``"C:\\...\\Code.exe" --open-url -- "%1"`` -> ``C:\\...\\Code.exe``. Also accepts the
    unquoted form, with spaces in the path. Returns None if there is nothing to extract.
    """
    s = (command or "").strip()
    if not s:
        return None
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 1 else None
    m = re.match(r"(.+?\.exe)(?=\s|$)", s, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return s.split()[0]


def read_registry_command():
    """Default value of ``HKCR\\vscode\\shell\\open\\command``, or None.

    Never raises: an unreadable registry leaves the reason on stderr and yields to the last resort.
    """
    try:
        import winreg
    except ImportError as e:
        _warn(f"registry: winreg not available ({e!r})")
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, REG_SUBKEY) as key:
            value, kind = winreg.QueryValueEx(key, "")
            if kind == winreg.REG_EXPAND_SZ and isinstance(value, str):
                value = winreg.ExpandEnvironmentStrings(value)
    except OSError as e:
        _warn(f"registry: could not read HKCR\\{REG_SUBKEY} ({e!r})")
        return None
    if not isinstance(value, str) or not value.strip():
        _warn(f"registry: HKCR\\{REG_SUBKEY} is empty")
        return None
    return value


def last_resort_candidates():
    """Paths to try when there is no ``--code-exe`` and no usable registry."""
    paths = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.append(os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe"))
    if CODE_EXE_LAST_RESORT not in paths:
        paths.append(CODE_EXE_LAST_RESORT)
    return paths


def _is_exe(path):
    return str(path).lower().endswith(".exe")


def resolve_code_exe(forced=None):
    """Returns ``(path, source)`` with source in {'--code-exe', 'registry', 'last-resort'}.

    Order: ``--code-exe`` (it wins and does not fall back to another route if it is wrong) -> command
    of the ``vscode`` protocol in the registry -> per-user path. Only a ``.exe`` is accepted: the
    ``code.cmd``/``.bat`` wrappers go through ``cmd.exe``, which reinterprets the ``%XX`` of the URI, and
    what was measured in Phase 0 is ``Code.exe``.
    """
    if forced:
        if not _is_exe(forced):
            raise TabLaunchError(
                f"--code-exe must be Code.exe, not a .cmd/.bat wrapper: {forced}")
        if not os.path.isfile(forced):
            raise TabLaunchError(f"--code-exe does not exist: {forced}")
        return str(forced), "--code-exe"

    command = read_registry_command()
    if command:
        exe = exe_from_command(command)
        if exe and _is_exe(exe) and os.path.isfile(exe):
            return exe, "registry"
        _warn(f"registry: the vscode protocol command does not point to an existing .exe "
              f"({exe!r}); trying the last resort")

    tried = last_resort_candidates()
    for path in tried:
        if os.path.isfile(path):
            return path, "last-resort"
    raise TabLaunchError(
        "Code.exe not found: neither in the registry (HKCR\\" + REG_SUBKEY + ") nor in "
        + " | ".join(tried) + ". Pass it with --code-exe")


# ---------------------------------------------------------------- launch

def launch_uri(code_exe, uri, wait_s=CODE_EXE_WAIT_S):
    """Runs ``Code.exe --open-url -- <uri>`` with the clean environment.

    Returns ``{"argv", "exit_code", "output"}``. ``exit_code`` is None if ``Code.exe`` did not
    finish within ``wait_s``: it is not killed (it may be the main VS Code instance) and
    the caller keeps polling.

    The output goes to a temporary file and not to a pipe: with pipes, a grandchild process that
    inherits the handle would leave the read hanging even though ``Code.exe`` had already exited.
    """
    argv = [code_exe, "--open-url", "--", uri]
    with tempfile.TemporaryFile() as capture:
        try:
            proc = subprocess.Popen(argv, env=child_env(), stdin=subprocess.DEVNULL,
                                    stdout=capture, stderr=subprocess.STDOUT)
        except OSError as e:
            raise TabLaunchError(f"could not run {code_exe}: {e!r}") from e
        try:
            exit_code = proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            _warn(f"Code.exe (pid {proc.pid}) did not finish in {wait_s:g} s; leaving it alive "
                  f"(it may be the main VS Code instance) and continuing to poll")
            return {"argv": argv, "exit_code": None, "output": ""}
        capture.seek(0)
        output = capture.read().decode("utf-8", errors="replace").strip()
    return {"argv": argv, "exit_code": exit_code, "output": output}


# ---------------------------------------------------------------- claude agents

def parse_agents(text):
    """``claude agents --json`` -> list of dicts. Accepts a list or an object with an ``agents`` key."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise TabLaunchError(f"claude agents --json did not return valid JSON ({e})") from e
    if isinstance(data, dict):
        data = data.get("agents")
    if not isinstance(data, list):
        raise TabLaunchError("claude agents --json returned an unexpected shape "
                             "(neither a list nor an object with an 'agents' key)")
    return [e for e in data if isinstance(e, dict)]


def list_agents(timeout_s=AGENTS_TIMEOUT_S):
    exe = shutil.which("claude")
    if not exe:
        raise TabLaunchError("the 'claude' executable was not found in PATH")
    try:
        r = subprocess.run([exe, "agents", "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                           timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise TabLaunchError(f"claude agents --json did not respond in {timeout_s:g} s") from e
    except OSError as e:
        raise TabLaunchError(f"could not run claude agents --json: {e!r}") from e
    if r.returncode != 0:
        raise TabLaunchError(f"claude agents --json returned exit {r.returncode}: "
                             f"{(r.stderr or '').strip()[:300]}")
    return parse_agents(r.stdout)


def live_ids(entries):
    return {str(e["sessionId"]) for e in entries if e.get("sessionId")}


def _summary(entry):
    return {"session_id": str(entry.get("sessionId") or ""),
            "native_name": entry.get("name"),
            "status": entry.get("status")}


def new_candidates(entries, snapshot):
    """``interactive`` sessions whose ``sessionId`` was not in the snapshot (no duplicates)."""
    fresh = {}
    for e in entries:
        sid = str(e.get("sessionId") or "")
        if not sid or sid in snapshot:
            continue
        if str(e.get("kind") or "").lower() != "interactive":
            continue
        fresh.setdefault(sid, _summary(e))
    return list(fresh.values())


def wait_for_tab(snapshot, timeout_s, t0, poll_s=POLL_S):
    """Polls until it sees exactly one new session, more than one, or the deadline runs out.

    Returns ``{"state", "candidates", "elapsed_s", "polls_ok", "polls_failed",
    "last_error"}`` with state in {'ok', 'ambiguous', 'timeout', 'no-data'}. A failed poll
    does not cut the wait (it leaves the reason on stderr); if NONE answered the state
    is 'no-data' and not 'timeout', so as not to blame the dialog for a broken ``claude``.
    """
    deadline = t0 + timeout_s
    polls_ok = polls_failed = 0
    last_error = None
    state, fresh = None, []
    while True:
        try:
            fresh = new_candidates(list_agents(), snapshot)
            polls_ok += 1
        except TabLaunchError as e:
            fresh = []
            polls_failed += 1
            if str(e) != last_error:
                _warn(f"poll failed: {e}")
            last_error = str(e)
        if len(fresh) == 1:
            state = "ok"
            break
        if len(fresh) > 1:
            state = "ambiguous"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            state = "timeout" if polls_ok else "no-data"
            break
        time.sleep(min(poll_s, remaining))
    return {"state": state, "candidates": fresh,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "polls_ok": polls_ok, "polls_failed": polls_failed,
            "last_error": last_error}


# ---------------------------------------------------------------- log

def log_event(event, thread_id, uri, **fields):
    """Append-only JSON line in ``LOG_PATH``. A write failure does not cut the launch
    but is left on stderr (rule 16)."""
    line = {"ts": now_art(), "event": event, "thread_id": thread_id, "uri": uri}
    line.update(fields)
    try:
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as e:
        _warn(f"could not write the log {LOG_PATH}: {e!r}")


# ---------------------------------------------------------------- CLI

class _Parser(argparse.ArgumentParser):
    """argparse exits with 2 on a usage error; here 2 means timeout."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def _seconds(value):
    try:
        n = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {value!r}") from e
    if not n > 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0: {value!r}")
    return n


def _parser():
    ap = _Parser(
        prog="tab_launch.py",
        description="Opens a new Claude Code tab in VS Code through a deep link and waits "
                    "until it is addressable.",
        epilog="Exit: 0 tab detected | 2 timeout (VS Code dialog unconfirmed) | "
               "1 error.")
    ap.add_argument("--thread", default=None, metavar="THREAD_ID",
                    help="thread the tab is going to resume: recorded in the log and "
                         "returned in the output. Does NOT preload any prompt")
    ap.add_argument("--prompt", default=None, metavar="TEXT",
                    help="text to preload in the box (typed, unsent). "
                         "Default: no prompt; the Router sends the first order")
    ap.add_argument("--timeout", type=_seconds, default=TIMEOUT_DEFAULT_S, metavar="S",
                    help=f"seconds to wait for the tab to appear "
                         f"(default {TIMEOUT_DEFAULT_S:g}; poll every {POLL_S:g} s)")
    ap.add_argument("--code-exe", default=None, metavar="PATH",
                    help="forces the path of Code.exe (default: vscode protocol "
                         "registry entry, otherwise the per-user install)")
    ap.add_argument("--dry-run", action="store_true",
                    help="prints executable, URI and the variables it removes, launching nothing")
    ap.add_argument("--json", action="store_true", help="JSON output on stdout")
    return ap


def _payload(thread_id, uri, session_id=None, native_name=None, status=None, elapsed_s=None):
    return {"session_id": session_id, "native_name": native_name, "status": status,
            "elapsed_s": elapsed_s, "thread_id": thread_id, "uri": uri}


def _print_json(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _fail(args, uri, message, exit_code, result, launched, elapsed_s=None, candidates=None):
    """Common exit for everything that is not exit 0: stderr, optional JSON and a log line."""
    print(f"tab_launch: {message}", file=sys.stderr)
    payload = _payload(args.thread, uri, elapsed_s=elapsed_s)
    payload["error"] = message
    if candidates:
        payload["candidates"] = candidates
    if args.json:
        _print_json(payload)
    if not args.dry_run:
        extra = {"candidates": candidates} if candidates else {}
        log_event("result", args.thread, uri, result=result, exit_code=exit_code,
                  launched=launched, elapsed_s=elapsed_s, error=message, **extra)
    return exit_code


def _dry_run(args, uri, code_exe, source, claude_exe):
    plan = {
        "dry_run": True,
        "code_exe": code_exe,
        "code_exe_source": source,
        "argv": [code_exe, "--open-url", "--", uri],
        "uri": uri,
        "env_removed": list(ENV_TO_REMOVE),
        "env_removed_present": present_variables(),
        "claude_exe": claude_exe,
        "thread_id": args.thread,
        "timeout_s": args.timeout,
        "poll_s": POLL_S,
    }
    if args.json:
        _print_json(plan)
        return EXIT_OK
    present = set(plan["env_removed_present"])
    print("[dry-run] nothing is launched and the log is not written")
    print(f"executable : {code_exe}  (source: {source})")
    print(f"arguments  : --open-url -- {uri}")
    print(f"uri        : {uri}")
    for var in ENV_TO_REMOVE:
        state = "present in this environment" if var in present else "not in this environment"
        print(f"removes    : {var}  ({state})")
    print(f"claude     : {claude_exe}")
    print(f"thread     : {args.thread or '(no thread)'}")
    print(f"wait       : {args.timeout:g} s, poll every {POLL_S:g} s")
    return EXIT_OK


def main(argv=None):
    args = _parser().parse_args(argv)
    uri = build_uri(args.prompt)
    launched = False
    try:
        if not is_windows():
            raise TabLaunchError(
                f"only runs on Windows (platform: {sys.platform}): it launches Code.exe")
        code_exe, source = resolve_code_exe(args.code_exe)
        claude_exe = shutil.which("claude")
        if not claude_exe:
            raise TabLaunchError("the 'claude' executable was not found in PATH: without it the "
                                 "new tab cannot be detected")
        if args.dry_run:
            return _dry_run(args, uri, code_exe, source, claude_exe)

        snapshot = live_ids(list_agents())
        t0 = time.monotonic()
        launch = launch_uri(code_exe, uri)
        if launch["exit_code"] not in (0, None):
            raise TabLaunchError(
                f"Code.exe returned exit {launch['exit_code']} (the URI was not opened): "
                f"{launch['output'][:300] or 'no output'}")
        launched = True
        log_event("launched", args.thread, uri, code_exe=code_exe, code_exe_source=source,
                  code_exit=launch["exit_code"], previous_sessions=len(snapshot))
        res = wait_for_tab(snapshot, args.timeout, t0)
    except TabLaunchError as e:
        return _fail(args, uri, str(e), EXIT_ERROR, "error", launched)
    except Exception as e:  # full trace on stderr and in the log, never silent
        traceback.print_exc()
        return _fail(args, uri, f"unexpected error: {type(e).__name__}: {e}",
                     EXIT_ERROR, "error", launched)

    state, elapsed = res["state"], res["elapsed_s"]

    if state == "ok":
        c = res["candidates"][0]
        payload = _payload(args.thread, uri, c["session_id"], c["native_name"], c["status"],
                           elapsed)
        log_event("result", args.thread, uri, result="ok", exit_code=EXIT_OK,
                  launched=True, session_id=c["session_id"], native_name=c["native_name"],
                  status=c["status"], elapsed_s=elapsed)
        if args.json:
            _print_json(payload)
        else:
            print(f"New tab detected in {elapsed:g} s")
            print(f"  session_id : {c['session_id']}")
            print(f"  native_name: {c['native_name']}")
            print(f"  status     : {c['status']}")
            print(f"  thread_id  : {args.thread or '(no thread)'}")
            print(f"  uri        : {uri}")
        return EXIT_OK

    if state == "ambiguous":
        listing = "; ".join(f"{c['native_name']} ({c['session_id']}, {c['status']})"
                            for c in res["candidates"])
        return _fail(
            args, uri,
            f"{len(res['candidates'])} new sessions appeared at once and there is no way to "
            f"tell which one was launched: {listing}",
            EXIT_ERROR, "ambiguous", True, elapsed, res["candidates"])

    if state == "no-data":
        return _fail(
            args, uri,
            f"the URI was launched but claude agents --json failed in all "
            f"{res['polls_failed']} polls, so it is unknown whether the tab opened: "
            f"{res['last_error']}",
            EXIT_ERROR, "error", True, elapsed)

    failed = (f" ({res['polls_failed']} polls failed: {res['last_error']})"
              if res["polls_failed"] else "")
    return _fail(
        args, uri,
        f"timeout: no new tab appeared in {args.timeout:g} s{failed}. "
        "Most likely the VS Code dialog \"Allow 'Claude Code for VS Code' "
        "extension to open this URI?\" is still unconfirmed: Open still has to be clicked (with \"Do not "
        "ask me again for this extension\" ticked it stops asking). If it is confirmed "
        "later the tab opens anyway: before relaunching, check claude agents --json.",
        EXIT_TIMEOUT, "timeout", True, elapsed)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
