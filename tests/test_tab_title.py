import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ledger  # noqa: E402

spec = importlib.util.spec_from_file_location("tab_title_hook", ROOT / "hooks" / "tab_title.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "data"))


def _run(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return hook.main()


def _transcript(tmp_path):
    p = tmp_path / "proj" / "sess-1.jsonl"
    p.parent.mkdir()
    p.write_text("{}\n", encoding="utf-8")
    return p


def test_compute_title():
    assert hook.compute_title(None) is None
    assert hook.compute_title({"role": "coord", "scope": None}) == "Router"
    assert hook.compute_title({"role": "child", "scope": "ledger"}, 7) == "r-7 ledger"
    assert hook.compute_title({"role": "child", "scope": "ledger"}) == "child ledger"


def test_coord_tab_gets_the_router_title(tmp_path, monkeypatch):
    t = _transcript(tmp_path)
    ledger.set_role("sess-1", "coord")
    assert _run(monkeypatch, {"session_id": "sess-1", "transcript_path": str(t)}) == 0
    last = json.loads(t.read_text(encoding="utf-8").splitlines()[-1])
    assert last["customTitle"] == "Router" and last["sessionId"] == "sess-1"
    sidecar = t.parent / "sess-1" / "custom-title.json"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {"customTitle": "Router"}


def test_child_tab_title_names_its_live_order(tmp_path, monkeypatch):
    t = _transcript(tmp_path)
    ledger.set_role("sess-1", "child", "port scripts")
    oid = ledger.assign("r", "o", "exit 0")
    ledger.accept(oid)
    _run(monkeypatch, {"session_id": "sess-1", "transcript_path": str(t)})
    assert json.loads(t.read_text(encoding="utf-8").splitlines()[-1])["customTitle"] == f"r-{oid} port scripts"


def test_no_role_changes_nothing_and_exits_zero(tmp_path, monkeypatch):
    t = _transcript(tmp_path)
    before = t.read_text(encoding="utf-8")
    assert _run(monkeypatch, {"session_id": "sess-1", "transcript_path": str(t)}) == 0
    assert t.read_text(encoding="utf-8") == before


def test_garbage_input_never_blocks_the_session(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
    assert _run(monkeypatch, {}) == 0
    assert _run(monkeypatch, {"session_id": "x", "transcript_path": "does-not-exist"}) == 0
