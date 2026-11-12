"""Environment check, run before the Router dispatches anything.

Measured requirements (see README): Claude Code 2.1.321 or later, the VS Code extension
2.1.322 or later, and Windows. The plugin relies on behaviors that are not documented
(the webview `viewType`, the 24-character cut of a tab title), and they fail on the safe side:
a tab that cannot be matched is not closed. This check turns a silent mismatch into a message.

Exit 0 = all requirements met, 1 = at least one is not.

Usage:
    python scripts/env_check.py [--claude-code X.Y.Z] [--extension X.Y.Z] [--platform win32]
Without flags it reads `claude --version` and the installed VS Code extension list.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

MIN_CLAUDE_CODE = (2, 1, 321)
MIN_EXTENSION = (2, 1, 322)
EXTENSION_ID = "anthropic.claude-code"


def parse_version(text) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def _fmt(v) -> str:
    return ".".join(str(x) for x in v)


def check(claude_code, extension, platform) -> list[str]:
    """Return one problem per unmet requirement. Empty list = all good."""
    problems = []
    if platform != "win32":
        problems.append(f"platform {platform!r} is untested: only Windows (win32) is supported")
    cc = parse_version(claude_code)
    if cc is None:
        problems.append("Claude Code version not found: is `claude` on the PATH?")
    elif cc < MIN_CLAUDE_CODE:
        problems.append(f"Claude Code {_fmt(cc)} is older than {_fmt(MIN_CLAUDE_CODE)}")
    ext = parse_version(extension)
    if ext is None:
        problems.append(f"VS Code extension {EXTENSION_ID} not found: install it from VS Code")
    elif ext < MIN_EXTENSION:
        problems.append(f"VS Code extension {_fmt(ext)} is older than {_fmt(MIN_EXTENSION)}")
    return problems


def _run(cmd) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        print(f"env_check: {cmd[0]} failed: {err}", file=sys.stderr)
        return ""
    return r.stdout


def detect_extension_version() -> str:
    for line in _run(["code", "--list-extensions", "--show-versions"]).splitlines():
        if line.lower().startswith(EXTENSION_ID + "@"):
            return line.split("@", 1)[1]
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the environment for claude-tab-dispatch.")
    ap.add_argument("--claude-code", default=None, help="version string; default: `claude --version`")
    ap.add_argument("--extension", default=None, help="version string; default: installed extension")
    ap.add_argument("--platform", default=sys.platform)
    args = ap.parse_args(argv)
    cc = args.claude_code if args.claude_code is not None else _run(["claude", "--version"])
    ext = args.extension if args.extension is not None else detect_extension_version()
    problems = check(cc, ext, args.platform)
    for p in problems:
        print(f"PROBLEM: {p}")
    if not problems:
        print(f"OK: Claude Code {_fmt(parse_version(cc))}, extension {_fmt(parse_version(ext))}, {args.platform}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
