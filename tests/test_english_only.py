"""The repository must stay English-only.

Scans every file git knows about (tracked, plus new files not yet committed and not ignored) and
fails with ``file:line`` when it finds Spanish: accented letters or inverted punctuation, or very
common Spanish function words used as whole words.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIXES = {".vsix", ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".woff", ".woff2"}
SKIP_DIRS = {".git", "node_modules"}

# Built from code points so this file itself stays pure ASCII.
ACCENT_RE = re.compile("[" + "".join(map(chr, (0xE1, 0xE9, 0xED, 0xF3, 0xFA, 0xF1, 0xBF, 0xA1, 0xC1, 0xC9, 0xCD, 0xD3, 0xDA, 0xD1, 0xFC, 0xDC))) + "]")

# Frequent Spanish function words that are not English words.
STOPWORDS = ("los", "las", "para", "sin", "con", "una", "pero", "cuando", "desde", "hasta",
             "porque", "tambien", "debe", "esta", "este", "estos", "del", "que", "como")
STOPWORD_RE = re.compile(r"\b(" + "|".join(STOPWORDS) + r")\b", re.IGNORECASE)

# This file lists the stop words, so it is exempt from that one check (not from the accent check).
SELF = Path(__file__).resolve()


def _repo_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        yield path


def _text(path):
    data = path.read_bytes()
    if b"\0" in data:
        return None  # binary
    return data.decode("utf-8", errors="replace")


def _findings():
    found = []
    for path in _repo_files():
        text = _text(path)
        if text is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for n, line in enumerate(text.splitlines(), 1):
            m = ACCENT_RE.search(line)
            if m:
                found.append(f"{rel}:{n}: non-English character {m.group()!r}: {line.strip()[:100]}")
                continue
            if path == SELF:
                continue
            m = STOPWORD_RE.search(line)
            if m:
                found.append(f"{rel}:{n}: Spanish word {m.group()!r}: {line.strip()[:100]}")
    return found


def test_repo_has_no_spanish_text():
    found = _findings()
    assert not found, "Spanish text found (translate it to English):\n" + "\n".join(found)
