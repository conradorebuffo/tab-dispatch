#!/usr/bin/env python
"""tab_control: close or focus a Claude Code tab through the conradorebuffo.tab-dispatch extension.

It is the MECHANISM: it does not decide whether a tab may be closed (that is the Router's policy). Steps of each action:

1. Checks that the target VS Code instance is running. If not, it launches nothing: with
   ``--open-url`` Code.exe would open a brand new VS Code.
2. Writes the REQUEST ``%LOCALAPPDATA%\\TabDispatch\\requests\\<nonce>.json`` (action, title, created).
   The extension only acts if that file exists, matches and is under 60 s old: the URI nonce
   alone authorizes nothing.
3. Launches ``Code.exe [--user-data-dir X] --open-url -- vscode://conradorebuffo.tab-dispatch/<action>?...``
   reusing ``resolve_code_exe`` and ``child_env`` from ``tab_launch.py``.
4. Waits for ``results/<nonce>.json`` up to ``--timeout`` and translates it to an exit code.

Exit: 0 ok | 1 error | 2 timeout | 4 rejected by the extension (not_found, duplicate, ...).
Trace in ``~/.claude/tab-dispatch/logs/tab_control.log``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tab_launch  # noqa: E402  (reuses resolve_code_exe, child_env, now_art)

URI_BASE = "vscode://conradorebuffo.tab-dispatch"
ACTIONS = ("list", "close", "focus")
TIMEOUT_DEFAULT_S = 15.0
POLL_S = 0.25
CODE_EXE_WAIT_S = 20.0
MEASURE_WAIT_S = 5.0
PROCESSES_TO_MEASURE = ("claude", "Code")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2
EXIT_REJECTED = 4

DATA_DIR = Path(os.environ.get("TAB_DISPATCH_HOME") or (Path.home() / ".claude" / "tab-dispatch"))
LOG_PATH = DATA_DIR / "logs" / "tab_control.log"


class TabControlError(Exception):
    """Failure that ends the run with exit 1 and a readable message."""


def base_dir():
    """Exchange folder with the extension (the same one ``extension.js`` uses)."""
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return Path(local) / "TabDispatch"


def build_uri(action, title, nonce):
    if action not in ACTIONS:
        raise TabControlError(f"invalid action: {action!r}")
    if action == "list":
        return f"{URI_BASE}/list?nonce={nonce}"
    if not title:
        raise TabControlError(f"{action} needs the tab title")
    # safe="" encodes '/', ':' and '&'; the space goes as %20, never '+'.
    return f"{URI_BASE}/{action}?title={quote(title, safe='')}&nonce={nonce}"


# ---------------------------------------------------------------- target instance

def _norm_path(text):
    return str(text).strip().strip('"').replace("/", "\\").rstrip("\\").lower()


def code_exe_lines():
    """Command lines of the ``Code.exe`` processes (PowerShell + CIM). Raises TabControlError."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='Code.exe'\" | "
          "Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TabControlError(f"could not list Code.exe processes: {e!r}") from e
    if r.returncode != 0:
        raise TabControlError(f"listing Code.exe processes failed (exit {r.returncode}): {r.stderr.strip()[:200]}")
    output = (r.stdout or "").strip()
    if not output:
        return []
    data = json.loads(output)
    if isinstance(data, str):
        data = [data]
    return [d for d in data if isinstance(d, str)]


def instance_running(user_data_dir, lines):
    """True if there is a MAIN Code.exe process for that instance.

    Electron child processes carry ``--type=``; the main one does not. Without ``user_data_dir`` the
    target instance is Conrado's main one: a main Code.exe without ``--user-data-dir``.
    """
    ud = _norm_path(user_data_dir) if user_data_dir else None
    for line in lines:
        if "--type=" in line:
            continue
        if ud is None:
            if "--user-data-dir" not in line:
                return True
        elif ud in line.replace("/", "\\").lower():
            return True
    return False


# ---------------------------------------------------------------- launch and wait

def launch(code_exe, uri, user_data_dir=None, wait_s=CODE_EXE_WAIT_S):
    """``Code.exe [--user-data-dir X] --open-url -- <uri>`` with the clean environment of tab_launch.

    Does not kill Code.exe if it does not finish within ``wait_s`` (it may be the main instance).
    """
    argv = [code_exe]
    if user_data_dir:
        argv += ["--user-data-dir", str(user_data_dir)]
    argv += ["--open-url", "--", uri]
    with tempfile.TemporaryFile() as capture:
        try:
            proc = subprocess.Popen(argv, env=tab_launch.child_env(), stdin=subprocess.DEVNULL,
                                    stdout=capture, stderr=subprocess.STDOUT)
        except OSError as e:
            raise TabControlError(f"could not run {code_exe}: {e!r}") from e
        try:
            exit_code = proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            return {"argv": argv, "exit_code": None, "output": ""}
        capture.seek(0)
        output = capture.read().decode("utf-8", errors="replace").strip()
    return {"argv": argv, "exit_code": exit_code, "output": output}


def wait_for_result(path, timeout_s, poll_s=POLL_S, clock=time.monotonic, sleep=time.sleep):
    """Returns the result dict, or None if ``timeout_s`` runs out."""
    deadline = clock() + timeout_s
    while True:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass  # the extension writes to .tmp and renames: a broken read is retried
        if clock() >= deadline:
            return None
        sleep(poll_s)


def exit_from_result(res):
    if res.get("ok"):
        return EXIT_OK
    if res.get("reason") == "error":
        return EXIT_ERROR
    return EXIT_REJECTED


# ---------------------------------------------------------------- memory

def measure_memory(query=None):
    """{process: {n, mb}} for claude and Code, using WorkingSet64. Quantities only."""
    query = query or _query_memory
    return query()


def _query_memory():
    ps = ("Get-Process -Name claude,Code -ErrorAction SilentlyContinue | "
          "Group-Object Name | ForEach-Object { [pscustomobject]@{ n=$_.Name; c=$_.Count; "
          "b=($_.Group | Measure-Object WorkingSet64 -Sum).Sum } } | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise TabControlError(f"memory measurement failed (exit {r.returncode}): {r.stderr.strip()[:200]}")
    text = (r.stdout or "").strip()
    data = json.loads(text) if text else []
    if isinstance(data, dict):
        data = [data]
    out = {p: {"n": 0, "mb": 0.0} for p in PROCESSES_TO_MEASURE}
    for d in data:
        name = str(d.get("n"))
        if name in out:
            out[name] = {"n": int(d.get("c") or 0), "mb": round((d.get("b") or 0) / 1048576, 1)}
    return out


def memory_delta(before, after):
    return {p: {"n": after[p]["n"] - before[p]["n"], "mb": round(after[p]["mb"] - before[p]["mb"], 1)}
            for p in before if p in after}


# ---------------------------------------------------------------- log

def log_event(event, **fields):
    """One JSON line per event. A log that cannot be written is reported on stderr (rule 16)."""
    line = {"ts": tab_launch.now_art(), "event": event, **fields}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[tab_control] could not write {LOG_PATH}: {e!r}", file=sys.stderr)


# ---------------------------------------------------------------- orchestration

def _request(action, title):
    p = {"action": action, "created": datetime.now(timezone.utc).isoformat()}
    if action != "list":
        p["title"] = title
    return p


def execute(action, title=None, user_data_dir=None, timeout_s=TIMEOUT_DEFAULT_S, measure=False,
            dry_run=False, base=None, code_exe=None, lines_fn=None, launch_fn=None,
            memory_fn=None, sleep=time.sleep, clock=time.monotonic, nonce=None,
            measure_wait_s=MEASURE_WAIT_S):
    """Runs an action and returns ``{"exit": int, "reason": str, ...}``. Never raises."""
    base = Path(base) if base else base_dir()
    nonce = nonce or str(uuid.uuid4())
    out = {"action": action, "title": title, "nonce": nonce, "user_data_dir": user_data_dir}
    try:
        uri = build_uri(action, title, nonce)
        out["uri"] = uri
        lines = (lines_fn or code_exe_lines)()
        running = instance_running(user_data_dir, lines)
        out["instance_running"] = running
        if not running:
            out.update(exit=EXIT_ERROR, reason="instance_not_running")
            log_event("no_instance", **out)
            return out
        if code_exe is None:
            code_exe, source = tab_launch.resolve_code_exe()
            out["code_exe_source"] = source
        out["code_exe"] = code_exe
        if dry_run:
            out.update(exit=EXIT_OK, reason="dry_run")
            return out

        before = measure_memory(memory_fn) if measure else None
        request_path = base / "requests" / f"{nonce}.json"
        result_path = base / "results" / f"{nonce}.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(_request(action, title), ensure_ascii=False), encoding="utf-8")
        log_event("request", action=action, title=title, nonce=nonce, user_data_dir=user_data_dir)
        try:
            launched = (launch_fn or launch)(code_exe, uri, user_data_dir)
            out["launch_exit"] = launched.get("exit_code")
            res = wait_for_result(result_path, timeout_s, sleep=sleep, clock=clock)
        finally:
            # An unconsumed request must not stay on disk (the extension also expires it after 60 s).
            try:
                request_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                print(f"[tab_control] could not delete the request {request_path}: {e!r}", file=sys.stderr)
        if res is None:
            out.update(exit=EXIT_TIMEOUT, reason="timeout")
        else:
            out["result"] = res
            out.update(exit=exit_from_result(res), reason=res.get("reason", "?"))
            try:
                result_path.unlink()
            except OSError as e:
                print(f"[tab_control] could not delete the result {result_path}: {e!r}", file=sys.stderr)
        if measure:
            sleep(measure_wait_s)
            after = measure_memory(memory_fn)
            out["memory"] = {"before": before, "after": after, "delta": memory_delta(before, after)}
    except TabControlError as e:
        out.update(exit=EXIT_ERROR, reason="error", detail=str(e))
    except Exception as e:  # noqa: BLE001 - the log keeps the full trace (rule 16)
        out.update(exit=EXIT_ERROR, reason="error", detail=repr(e))
        log_event("exception", action=action, title=title, trace=traceback.format_exc())
    log_event("end", **{k: v for k, v in out.items() if k != "result"}, ok=out.get("exit") == EXIT_OK)
    return out


def _parser():
    p = argparse.ArgumentParser(prog="tab_control", description=__doc__.split("\n\n")[0])
    p.add_argument("action", choices=ACTIONS)
    p.add_argument("title", nargs="?", help="exact title of the tab (close and focus)")
    p.add_argument("--user-data-dir", help="target instance; without it, the main one")
    p.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT_S)
    p.add_argument("--measure", action="store_true", help="memory of claude and Code before and 5 s after")
    p.add_argument("--dry-run", action="store_true", help="builds the URI and checks the instance; does not launch")
    p.add_argument("--code-exe")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    from stdio_utf8 import utf8_stdio
    utf8_stdio()
    args = _parser().parse_args(argv)
    code_exe = None
    if args.code_exe:
        try:
            code_exe, _ = tab_launch.resolve_code_exe(args.code_exe)
        except tab_launch.TabLaunchError as e:
            print(f"[tab_control] {e}", file=sys.stderr)
            return EXIT_ERROR
    out = execute(args.action, args.title, args.user_data_dir, args.timeout, args.measure,
                  args.dry_run, code_exe=code_exe)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"{out['action']} {out.get('title') or ''}: {out['reason']} (exit {out['exit']})")
        if out.get("detail"):
            print(out["detail"], file=sys.stderr)
    return out["exit"]


if __name__ == "__main__":
    sys.exit(main())
