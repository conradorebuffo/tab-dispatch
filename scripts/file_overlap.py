"""Simple cross-tab file check, run before dispatching an order.

Two warnings, both from data the plugin already has:
  1. two live orders declare the same file;
  2. a file the new order declares is already modified in the working tree (`git status`).

It is a notice, not a lock. For full protection against two tabs editing one file, use a
real guard such as cotenancy-guard (the README says so).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def modified_files(porcelain: str) -> set[str]:
    """Paths from `git status --porcelain` output (renames count their new path)."""
    out = set()
    for ln in porcelain.splitlines():
        if len(ln) < 4:
            continue
        path = ln[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            out.add(path.replace("\\", "/"))
    return out


def warnings(new_files, live: dict, porcelain: str = "") -> list[str]:
    """`live` maps order id -> declared files. Returns one line per problem."""
    new = {f.replace("\\", "/") for f in new_files}
    out = []
    for oid, files in sorted(live.items()):
        shared = sorted(new & {f.replace("\\", "/") for f in files})
        if shared:
            out.append(f"r-{oid} already declares: {', '.join(shared)}")
    dirty = sorted(new & modified_files(porcelain))
    if dirty:
        out.append(f"already modified in the working tree: {', '.join(dirty)}")
    return out


def main(argv=None) -> int:
    from stdio_utf8 import utf8_stdio
    utf8_stdio()
    import argparse

    import ledger
    ap = argparse.ArgumentParser(description="Warn if a new order's files overlap live orders or local changes.")
    ap.add_argument("--files", required=True, help="comma-separated files the new order declares")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    git = subprocess.run(["git", "-C", args.repo, "status", "--porcelain"], capture_output=True, text=True)
    if git.returncode != 0:
        print(f"ERROR: git status failed: {git.stderr.strip()}", file=sys.stderr)
        return 2
    found = warnings([f for f in args.files.split(",") if f.strip()], ledger.live_files(), git.stdout)
    print(json.dumps(found, ensure_ascii=False, indent=2))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
