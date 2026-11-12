"""Done criterion: the verdict is a command, and the command has an exit code.

A tab that says "done" has not said anything checkable. The Router writes the check
into the order (`done:`), and the order closes only when that command exits 0. The one
who does the work does not write the criterion, and the criterion does not ask the one
who did the work.

Fails closed: a command that cannot start, or that outlives the timeout, is "not verified".

Usage:
    python scripts/done_criterio.py --check REF "COMMAND" [--cwd DIR] [--json]
Exit 0 = verified, 1 = not verified.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

#: A criterion that hangs blocks the close that came to verify it.
TIMEOUT_S = 120


def check_done(ref, criterion_cmd, cwd=None):
    """Run the criterion and return (verified: bool, detail: dict)."""
    base = {"ref": ref, "criterion": criterion_cmd}
    try:
        # UTF-8 with replacement: on Windows the default decoder is the ANSI code page, which
        # turned a check mark into mojibake and made the reader thread raise UnicodeDecodeError
        # (stdout recorded as ""). PYTHONIOENCODING makes a Python criterion write UTF-8 too.
        r = subprocess.run(criterion_cmd, shell=True, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           timeout=TIMEOUT_S, cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired:
        return False, {**base, "verified": False, "exit_code": None,
                       "reason": f"the criterion did not finish in {TIMEOUT_S}s"}
    except OSError as err:
        return False, {**base, "verified": False, "exit_code": None,
                       "reason": f"could not run the criterion: {err}"}
    ok = r.returncode == 0
    detail = {**base, "verified": ok, "exit_code": r.returncode,
              "stdout": (r.stdout or "").strip()[:500]}
    if not ok:
        detail["reason"] = (r.stderr or "").strip()[:500] or "non-zero exit code, no stderr"
    return ok, detail


def main(argv=None) -> int:
    from stdio_utf8 import utf8_stdio
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Verdict of the done criterion, by exit code.")
    ap.add_argument("--check", nargs=2, metavar=("REF", "CMD"), required=True,
                    help="Run CMD as the done criterion for REF. Exit 0 = verified.")
    ap.add_argument("--cwd", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ref, cmd = args.check
    ok, detail = check_done(ref, cmd, cwd=args.cwd)
    if args.json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    else:
        print(f"{'VERIFIED' if ok else 'NOT VERIFIED'}  {ref}  (exit={detail['exit_code']})")
        if not ok:
            print(f"  reason: {detail.get('reason', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
