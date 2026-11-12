"""Make a CLI's stdout and stderr UTF-8, whatever the console code page is.

On Windows a redirected or console stdout defaults to the ANSI code page (cp1252). The CLIs
print JSON with `ensure_ascii=False`, and since 0.1.1 that JSON can carry the real output of a
done command (node prints U+2714, `claude plugin validate` prints U+26A0). Printing it to
cp1252 raised UnicodeEncodeError after `ledger close` had already closed the order, so a
verified order exited 1. Call `utf8_stdio()` first in every `main()`.
"""
from __future__ import annotations

import sys


def utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
