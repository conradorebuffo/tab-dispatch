"""Tests for tab_control.py. No real VS Code: launcher and result are simulated."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tab_control as tc  # noqa: E402


@pytest.fixture(autouse=True)
def temp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "LOG_PATH", tmp_path / "logs" / "tab_control.log")


MAIN = ['"C:\\VS\\Code.exe"', '"C:\\VS\\Code.exe" --type=renderer --foo']
GLM = ['"C:\\VS\\Code.exe" --user-data-dir C:\\Users\\x\\AppData\\Local\\VSCode-GLM']
FAKE_EXE = "C:\\VS\\Code.exe"


class Extension:
    """Simulates the extension: on launch it requires the request on disk and writes the result."""

    def __init__(self, base, result, write=True):
        self.base, self.result, self.write = base, result, write
        self.calls = []
        self.seen_request = None

    def __call__(self, code_exe, uri, user_data_dir=None):
        self.calls.append((code_exe, uri, user_data_dir))
        nonce = uri.split("nonce=")[1]
        request = self.base / "requests" / f"{nonce}.json"
        self.seen_request = json.loads(request.read_text(encoding="utf-8")) if request.is_file() else None
        if self.write:
            d = self.base / "results"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{nonce}.json").write_text(json.dumps(self.result), encoding="utf-8")
        return {"argv": [], "exit_code": 0, "output": ""}


def run(tmp_path, ext, action="close", title="S1-Short", lines=MAIN, **kw):
    return tc.execute(action, title, base=tmp_path / "ks", code_exe=FAKE_EXE,
                      lines_fn=lambda: lines, launch_fn=ext, sleep=lambda s: None,
                      timeout_s=kw.pop("timeout_s", 1.0), **kw)


def test_ok_writes_request_before_launching_and_cleans_up(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "reason": "ok", "match": "exact"})
    out = run(tmp_path, ext)
    assert out["exit"] == tc.EXIT_OK
    assert ext.seen_request["action"] == "close" and ext.seen_request["title"] == "S1-Short"
    assert "created" in ext.seen_request
    assert list((base / "requests").glob("*.json")) == []
    assert list((base / "results").glob("*.json")) == []
    uri = ext.calls[0][1]
    assert uri.startswith("vscode://conradorebuffo.tab-dispatch/close?title=S1-Short&nonce=")


def test_title_with_spaces_and_ampersand_is_encoded(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "reason": "ok"})
    run(tmp_path, ext, title="Claude Code & more")
    assert "title=Claude%20Code%20%26%20more&nonce=" in ext.calls[0][1]


@pytest.mark.parametrize("reason", ["not_found", "duplicate", "unsupported: x"])
def test_extension_rejection_is_exit_4(tmp_path, reason):
    ext = Extension(tmp_path / "ks", {"ok": False, "reason": reason})
    out = run(tmp_path, ext)
    assert out["exit"] == tc.EXIT_REJECTED
    assert out["reason"] == reason


def test_extension_error_is_exit_1(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": False, "reason": "error", "detail": "boom"})
    assert run(tmp_path, ext)["exit"] == tc.EXIT_ERROR


def test_timeout_is_exit_2_and_deletes_the_request(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {}, write=False)
    t = {"v": 0.0}

    def clock():
        t["v"] += 0.6
        return t["v"]

    out = tc.execute("close", "S1-Short", base=base, code_exe=FAKE_EXE, lines_fn=lambda: MAIN,
                     launch_fn=ext, sleep=lambda s: None, clock=clock, timeout_s=1.0)
    assert out["exit"] == tc.EXIT_TIMEOUT and out["reason"] == "timeout"
    assert list((base / "requests").glob("*.json")) == []


def test_instance_not_running_does_not_launch_or_write_a_request(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "reason": "ok"})
    out = run(tmp_path, ext, lines=[])
    assert out["exit"] == tc.EXIT_ERROR and out["reason"] == "instance_not_running"
    assert ext.calls == []
    assert not (base / "requests").exists()


def test_only_electron_children_do_not_count_as_an_instance(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "reason": "ok"})
    out = run(tmp_path, ext, lines=['"C:\\VS\\Code.exe" --type=renderer'])
    assert out["reason"] == "instance_not_running"


def test_main_is_not_confused_with_code_glm(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "reason": "ok"})
    # Only code-glm is running: the main instance is NOT running.
    assert run(tmp_path, ext, lines=GLM)["reason"] == "instance_not_running"
    # With the --user-data-dir of code-glm, it is.
    ud = "C:\\Users\\x\\AppData\\Local\\VSCode-GLM"
    out = run(tmp_path, ext, lines=GLM, user_data_dir=ud)
    assert out["exit"] == tc.EXIT_OK
    assert ext.calls[0][2] == ud


def test_dry_run_neither_launches_nor_writes(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "reason": "ok"})
    out = run(tmp_path, ext, dry_run=True)
    assert out["exit"] == tc.EXIT_OK and out["reason"] == "dry_run"
    assert ext.calls == [] and not (base / "requests").exists()


def test_close_without_a_title_is_an_error(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "reason": "ok"})
    out = run(tmp_path, ext, title=None)
    assert out["exit"] == tc.EXIT_ERROR and ext.calls == []


def test_list_carries_no_title(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "reason": "ok", "tabs": []})
    out = run(tmp_path, ext, action="list", title=None)
    assert out["exit"] == tc.EXIT_OK
    assert "title" not in ext.seen_request
    assert ext.calls[0][1].startswith("vscode://conradorebuffo.tab-dispatch/list?nonce=")


def test_measure_computes_delta(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "reason": "ok"})
    snapshots = iter([
        {"claude": {"n": 5, "mb": 1000.0}, "Code": {"n": 10, "mb": 2000.0}},
        {"claude": {"n": 4, "mb": 680.0}, "Code": {"n": 10, "mb": 1990.5}},
    ])
    waits = []
    out = tc.execute("close", "S1-Short", base=tmp_path / "ks", code_exe=FAKE_EXE, lines_fn=lambda: MAIN,
                     launch_fn=ext, sleep=waits.append, timeout_s=1.0, measure=True,
                     memory_fn=lambda: next(snapshots))
    assert out["memory"]["delta"] == {"claude": {"n": -1, "mb": -320.0}, "Code": {"n": 0, "mb": -9.5}}
    assert tc.MEASURE_WAIT_S in waits


def test_launch_removes_electron_run_as_node_and_passes_user_data_dir(monkeypatch):
    seen = {}

    class P:
        pid = 1

        def wait(self, timeout=None):
            return 0

    def popen(argv, env=None, **kw):
        seen["argv"], seen["env"] = argv, env
        return P()

    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    monkeypatch.setattr(tc.subprocess, "Popen", popen)
    r = tc.launch(FAKE_EXE, "vscode://conradorebuffo.tab-dispatch/list?nonce=n", "C:\\ud")
    assert r["exit_code"] == 0
    assert "ELECTRON_RUN_AS_NODE" not in seen["env"]
    assert seen["argv"] == [FAKE_EXE, "--user-data-dir", "C:\\ud", "--open-url", "--",
                            "vscode://conradorebuffo.tab-dispatch/list?nonce=n"]


def test_launch_without_user_data_dir(monkeypatch):
    seen = {}

    class P:
        pid = 1

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(tc.subprocess, "Popen", lambda argv, **kw: seen.update(argv=argv) or P())
    tc.launch(FAKE_EXE, "u")
    assert seen["argv"] == [FAKE_EXE, "--open-url", "--", "u"]


def test_the_log_leaves_a_trace(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "reason": "ok"})
    run(tmp_path, ext)
    events = [json.loads(x)["event"] for x in tc.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert events == ["request", "end"]
