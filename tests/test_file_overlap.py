import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import file_overlap as fo  # noqa: E402
import ledger  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path))


def test_two_orders_declaring_the_same_file_warn():
    first = ledger.assign("r1", "o", "exit 0", files=["src/a.py", "src/b.py"])
    found = fo.warnings(["src/b.py", "src/c.py"], ledger.live_files())
    assert found == [f"r-{first} already declares: src/b.py"]


def test_disjoint_orders_do_not_warn():
    ledger.assign("r1", "o", "exit 0", files=["a.py"])
    assert fo.warnings(["b.py"], ledger.live_files()) == []


def test_closed_or_dropped_orders_do_not_count():
    oid = ledger.assign("r1", "o", "exit 0", files=["a.py"])
    ledger.accept(oid)
    ledger.report(oid, "dropped", "cancelled")
    assert fo.warnings(["a.py"], ledger.live_files()) == []


def test_file_already_modified_in_the_working_tree_warns():
    porcelain = " M src/a.py\n?? notes.txt\nR  old.py -> new.py\n"
    assert fo.modified_files(porcelain) == {"src/a.py", "notes.txt", "new.py"}
    assert fo.warnings(["new.py"], {}, porcelain) == ["already modified in the working tree: new.py"]


def test_backslash_paths_are_normalized():
    ledger.assign("r1", "o", "exit 0", files=["src\\a.py"])
    assert fo.warnings(["src/a.py"], ledger.live_files())
