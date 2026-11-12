"""Keep the repo free of compiled files.

A .pyc embeds the absolute path of the machine that ran the tests, so a checked-out tree that
was tested would carry a local path. Stop new ones, and remove the one pytest wrote for this
file before it ran (it is written first, then the module executes).
"""
import pathlib
import sys

sys.dont_write_bytecode = True

_cache = pathlib.Path(__file__).resolve().parent / "__pycache__"
for _pyc in _cache.glob("conftest.*.pyc"):
    try:
        _pyc.unlink()
    except OSError as err:
        print(f"conftest: could not remove {_pyc.name}: {err}", file=sys.stderr)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_session(tmp_path, monkeypatch):
    """Tests never touch the real Claude Code session of the tab that runs them.

    `ledger accept` and `ledger role` write a tab title for the current session; inside a
    Claude Code tab that would be the tab running pytest. Point the projects folder to a
    temporary one and drop the session id, so a test only sees what it creates.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
