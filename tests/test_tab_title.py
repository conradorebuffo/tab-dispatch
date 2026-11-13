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
    assert last["customTitle"] == "S1-Router" and last["sessionId"] == "sess-1"
    sidecar = t.parent / "sess-1" / "custom-title.json"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {"customTitle": "S1-Router"}


def test_child_tab_title_is_its_session_number_and_scope(tmp_path, monkeypatch):
    t = _transcript(tmp_path)
    ledger.set_role("sess-1", "child", "port scripts")
    oid = ledger.assign("r", "o", "exit 0")
    ledger.accept(oid, session_id="sess-1")
    _run(monkeypatch, {"session_id": "sess-1", "transcript_path": str(t)})
    assert json.loads(t.read_text(encoding="utf-8").splitlines()[-1])["customTitle"] == "S1-PortScripts"


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


def _project_transcript(tmp_path, session_id):
    """A transcript where Claude Code keeps it: <config>/projects/<project>/<session>.jsonl."""
    p = tmp_path / "claude-config" / "projects" / "c--work-repo" / f"{session_id}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}\n", encoding="utf-8")
    return p


def _last_title(path):
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["customTitle"]


def test_each_child_session_gets_its_own_title(tmp_path, monkeypatch):
    """Three children accept three orders: each title is that session's own, not the newest one."""
    sessions = {"sess-a": "check-python", "sess-b": "check-node", "sess-c": "check-manifest"}
    paths = {s: _project_transcript(tmp_path, s) for s in sessions}
    ids = {}
    for sid, scope in sessions.items():
        ledger.set_role(sid, "child", scope)
        ids[sid] = ledger.assign(scope, "o", "exit 0")
    for sid in sessions:  # accepted in order: before 0.1.1 all three got the last one
        ledger.accept(ids[sid], session_id=sid)
    want = {"sess-a": "S1-CheckPython", "sess-b": "S2-CheckNode", "sess-c": "S3-CheckManifest"}
    for sid, scope in sessions.items():
        assert _last_title(paths[sid]) == want[sid]
        sidecar = paths[sid].parent / sid / "custom-title.json"
        assert json.loads(sidecar.read_text(encoding="utf-8")) == {"customTitle": want[sid]}
    # The SessionStart hook agrees with what accept wrote, session by session.
    for sid, scope in sessions.items():
        _run(monkeypatch, {"session_id": sid, "transcript_path": str(paths[sid])})
        assert _last_title(paths[sid]) == want[sid]


def test_accept_takes_the_session_from_the_environment(tmp_path, monkeypatch):
    p = _project_transcript(tmp_path, "sess-env")
    ledger.set_role("sess-env", "child", "env")
    oid = ledger.assign("r", "o", "exit 0")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-env")
    row = ledger.accept(oid)
    assert row["session_id"] == "sess-env"
    assert _last_title(p) == "S1-Env"


def test_an_order_accepted_by_another_session_does_not_change_my_title(tmp_path, monkeypatch):
    mine = _project_transcript(tmp_path, "sess-mine")
    ledger.set_role("sess-mine", "child", "mine")
    oid = ledger.assign("r", "o", "exit 0")
    ledger.accept(oid, session_id="sess-other")
    _run(monkeypatch, {"session_id": "sess-mine", "transcript_path": str(mine)})
    assert _last_title(mine) == "S1-Mine"


def test_role_writes_the_title_without_a_session_restart(tmp_path):
    p = _project_transcript(tmp_path, "sess-r")
    ledger.set_role("sess-r", "coord")
    assert _last_title(p) == "S1-Router"


def test_git_bash_transcript_path_is_converted_on_windows(monkeypatch):
    import titles
    monkeypatch.setattr(titles.os, "name", "nt", raising=False)
    assert titles.native_path("/c/Users/me/.claude/x.jsonl") == r"C:\Users\me\.claude\x.jsonl"
    assert titles.native_path(r"C:\already\native.jsonl") == r"C:\already\native.jsonl"


def test_hook_titles_a_session_given_a_git_bash_path(tmp_path, monkeypatch):
    """The 0.1.0 hook exited 0 and wrote nothing when the path came as /c/... (no log)."""
    import titles
    p = _project_transcript(tmp_path, "sess-gb")
    ledger.set_role("sess-gb", "coord")
    posix = "/" + str(p)[0].lower() + str(p)[2:].replace("\\", "/")
    monkeypatch.setattr(titles, "native_path", lambda path: str(p) if path == posix else path)
    monkeypatch.setattr(hook, "native_path", lambda path: str(p) if path == posix else path)
    before = len(p.read_text(encoding="utf-8").splitlines())
    _run(monkeypatch, {"session_id": "sess-gb", "transcript_path": posix})
    assert len(p.read_text(encoding="utf-8").splitlines()) == before + 1
