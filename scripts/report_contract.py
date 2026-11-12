"""Parser and validator for the two text blocks of the contract (docs/CONTRACT.md).

    [ORDER r-<id>]   Router -> child tab, at most 1500 characters.
    [REPORT r-<id>]  child tab -> Router, at most 600 characters.

Both start with a stamp line `[YYYY-MM-DD HH:MM <offset>] [KIND r-<id>]`. A block that
does not parse is not a message of the protocol: nothing acts on it.

Usage: pipe a block on stdin: `python scripts/report_contract.py < block.txt`
Exit 0 = valid, 1 = invalid.
"""
from __future__ import annotations

import re
import sys

ORDER_MAX = 1500
REPORT_MAX = 600
REPORT_STATES = ("ready", "waiting", "blocked", "failed", "dropped")
ORDER_FIELDS = ("ref", "objective", "done", "limits", "authority", "expires", "reply")

_HEAD = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?: [+-]\d{4})?\] \[(ORDER|REPORT) r-(\d+)\]$")


class ContractError(ValueError):
    pass


def _split(text: str):
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        raise ContractError("empty block")
    head = _HEAD.match(lines[0])
    if not head:
        raise ContractError("first line must be `[stamp] [ORDER r-<id>]` or `[stamp] [REPORT r-<id>]`")
    fields = {}
    for ln in lines[1:]:
        key, sep, value = ln.partition(": ")
        if not sep or not re.fullmatch(r"[a-z]+", key):
            raise ContractError(f"line is not `field: value`: {ln[:40]!r}")
        if key in fields:
            raise ContractError(f"duplicate field `{key}`")
        fields[key] = value.strip()
    return head.group(1), int(head.group(2)), fields


def parse_report(text: str) -> dict:
    """Validate a [REPORT] block and return {id, state, evidence, question}."""
    if len(text) > REPORT_MAX:
        raise ContractError(f"report has {len(text)} characters (max {REPORT_MAX})")
    kind, rid, f = _split(text)
    if kind != "REPORT":
        raise ContractError("not a REPORT block")
    if f.get("state") not in REPORT_STATES:
        raise ContractError(f"state must be one of {'|'.join(REPORT_STATES)}")
    if not f.get("evidence"):
        raise ContractError("evidence is required")
    extra = set(f) - {"state", "evidence", "question"}
    if extra:
        raise ContractError(f"unknown field(s): {', '.join(sorted(extra))}")
    if f["state"] == "waiting" and not f.get("question"):
        raise ContractError("waiting needs a question")
    if f["state"] != "waiting" and "question" in f:
        raise ContractError("question only goes with waiting")
    return {"id": rid, "state": f["state"], "evidence": f["evidence"], "question": f.get("question")}


def parse_order(text: str) -> dict:
    """Validate an [ORDER] block and return its fields plus the id."""
    if len(text) > ORDER_MAX:
        raise ContractError(f"order has {len(text)} characters (max {ORDER_MAX})")
    kind, rid, f = _split(text)
    if kind != "ORDER":
        raise ContractError("not an ORDER block")
    missing = [k for k in ORDER_FIELDS if k not in f]
    if missing:
        raise ContractError(f"missing field(s): {', '.join(missing)}")
    return {"id": rid, **f}


def main() -> int:
    text = sys.stdin.read()
    first = text.strip().splitlines()[0] if text.strip() else ""
    try:
        block = parse_report(text) if "[REPORT " in first else parse_order(text)
    except ContractError as err:
        print(f"INVALID: {err}", file=sys.stderr)
        return 1
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
