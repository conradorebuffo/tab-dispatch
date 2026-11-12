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
