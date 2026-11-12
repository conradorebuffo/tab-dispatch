# -*- coding: utf-8 -*-
"""Tests for tab_launch.py.

NO test launches Code.exe or runs `claude agents`: the `subprocess` module that
tab_launch sees is replaced by a double, as are `shutil.which`, the clock and the registry.
The log goes to tmp_path. Neither the DB nor the real workspace environment is touched.
"""
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tab_launch  # noqa: E402

VAR = "ELECTRON_RUN_AS_NODE"
OUTPUT_KEYS = {"session_id", "native_name", "status", "elapsed_s", "thread_id", "uri"}


def _agent(sid, name, status="idle", kind="interactive"):
    return {"pid": 1, "cwd": "c:\\work", "kind": kind, "startedAt": 1793826600887,
            "sessionId": sid, "name": name, "status": status}


OLD_1 = _agent("11111111-0000-4000-8000-000000000001", "T1111-ParentTab", "busy")
OLD_2 = _agent("22222222-0000-4000-8000-000000000002", "myproject-5c")
NEW = _agent("bbbbbbbb-0000-4000-8000-0000000000b3", "myproject-b3")
NEW_2 = _agent("cccccccc-0000-4000-8000-0000000000c0", "myproject-c0")
BASE = [OLD_1, OLD_2]


class FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode
        self.pid = 4242

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("Code.exe", timeout)
        return self.returncode


class FakeSubprocess:
    """Double of the subprocess module: Popen = Code.exe, run = `claude agents --json`.

    `agents` is the sequence of `claude agents` responses; the last one repeats.
    Each response is a list/dict (serialized), a str (raw stdout), a tuple
    (exit, stdout, stderr) or an exception to raise.
    """
    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT
    DEVNULL = subprocess.DEVNULL
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, agents, code_exit=0, code_output="", popen_error=None):
        self.agents = list(agents)
        self.code_exit = code_exit
        self.code_output = code_output
        self.popen_error = popen_error
        self.events = []
        self.popen_calls = []
        self.run_calls = []

    def Popen(self, argv, **kw):
        self.events.append("popen")
        self.popen_calls.append({"argv": list(argv), "env": kw.get("env"), "kw": kw})
        if self.popen_error:
            raise self.popen_error
        target = kw.get("stdout")
        if self.code_output and hasattr(target, "write"):
            target.write(self.code_output.encode("utf-8"))
        return FakeProc(self.code_exit)

    def run(self, argv, **kw):
        self.events.append("run")
        self.run_calls.append(list(argv))
        r = self.agents[min(len(self.run_calls), len(self.agents)) - 1]
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, tuple):
            rc, out, err = r
        elif isinstance(r, str):
            rc, out, err = 0, r, ""
        else:
            rc, out, err = 0, json.dumps(r), ""
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeTime:
    """Clock that only advances when the code sleeps: no test really waits."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Harness:
    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.log = tmp_path / "logs" / "tab_launch.log"
        self.code_exe = tmp_path / "vscode" / "Code.exe"
        self.code_exe.parent.mkdir()
        self.code_exe.write_bytes(b"")
        self.clock = FakeTime()
        self.fake = None
        monkeypatch.setattr(tab_launch, "LOG_PATH", self.log)
        monkeypatch.setattr(tab_launch, "is_windows", lambda: True)
        monkeypatch.setattr(tab_launch, "read_registry_command",
                            lambda: f'"{self.code_exe}" --open-url -- "%1"')
        monkeypatch.setattr(tab_launch, "time", self.clock)
        self.claude("C:\\fake\\npm\\claude.CMD")
        self.subprocess([BASE])

    def claude(self, path):
        self.mp.setattr(tab_launch, "shutil", types.SimpleNamespace(which=lambda _n: path))

    def subprocess(self, agents, **kw):
        self.fake = FakeSubprocess(agents, **kw)
        self.mp.setattr(tab_launch, "subprocess", self.fake)
        return self.fake

    def log_lines(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


def _json_stdout(capsys):
    out = capsys.readouterr()
    return json.loads(out.out), out.err


# ------------------------------------------------------------------ child environment

def test_child_env_removes_the_variable_case_insensitively():
    base = {"Electron_Run_As_Node": "1", "PATH": "x", "ELECTRON_NO_ATTACH_CONSOLE": "1"}
    child = tab_launch.child_env(base)
    assert sorted(child) == ["ELECTRON_NO_ATTACH_CONSOLE", "PATH"]
    assert "Electron_Run_As_Node" in base  # the source dict is not mutated


def test_child_without_electron_run_as_node_even_if_the_parent_has_it(harness, monkeypatch, capsys):
    monkeypatch.setenv(VAR, "1")
    fake = harness.subprocess([BASE, BASE + [NEW]])

    assert tab_launch.main(["--json"]) == 0

    (call,) = fake.popen_calls
    env = call["env"]
    assert env is not None, "env=None inherits the parent environment: exactly the F0-i bug"
    child_has_it = any(k.upper() == VAR for k in env)
    assert not child_has_it
    parent_has_it = VAR in os.environ
    assert parent_has_it, "the parent environment is not touched"
    keeps_the_rest = env.get("PATH") == os.environ.get("PATH")
    assert keeps_the_rest
    assert call["argv"] == [str(harness.code_exe), "--open-url", "--", tab_launch.URI_BASE]


def test_claude_agents_is_not_run_with_a_shell(harness, capsys):
    fake = harness.subprocess([BASE, BASE + [NEW]])
    assert tab_launch.main(["--json"]) == 0
    assert fake.run_calls[0] == ["C:\\fake\\npm\\claude.CMD", "agents", "--json"]
    assert "shell" not in fake.popen_calls[0]["kw"]


# ------------------------------------------------------------------ URI

def test_uri_without_prompt_is_the_base():
    assert tab_launch.build_uri() == "vscode://anthropic.claude-code/open"
    assert tab_launch.build_uri("") == tab_launch.URI_BASE
    assert tab_launch.build_uri("   ") == tab_launch.URI_BASE


def test_uri_urlencodes_the_prompt():
    prompt = "/session-start-fast thread:thread-x & m\u00e1s=\u00f1 #1 50%+a/b?c\n\"end\""
    uri = tab_launch.build_uri(prompt)
    assert uri.startswith("vscode://anthropic.claude-code/open?prompt=")
    raw = uri.split("?prompt=", 1)[1]
    # Only unreserved characters and escapes: no URI separator left unencoded.
    assert re.fullmatch(r"[A-Za-z0-9._~%-]+", raw), raw
    assert "%20" in raw and "+" not in raw  # space as %20, never '+'
    assert unquote(raw) == prompt
    assert parse_qs(urlsplit(uri).query, keep_blank_values=True)["prompt"] == [prompt]


def test_uri_from_the_plan_example():
    uri = tab_launch.build_uri("/session-start-fast thread:thread-demo")
    assert uri == ("vscode://anthropic.claude-code/open"
                   "?prompt=%2Fsession-start-fast%20thread%3Athread-demo")


def test_prompt_reaches_code_exe_encoded(harness, capsys):
    fake = harness.subprocess([BASE, BASE + [NEW]])
    assert tab_launch.main(["--prompt", "hello world & m\u00e1s", "--json"]) == 0
    payload, _ = _json_stdout(capsys)
    expected = tab_launch.URI_BASE + "?prompt=hello%20world%20%26%20m%C3%A1s"
    assert fake.popen_calls[0]["argv"][-1] == expected
    assert payload["uri"] == expected


def test_thread_does_not_preload_a_prompt(harness, capsys):
    fake = harness.subprocess([BASE, BASE + [NEW]])
    assert tab_launch.main(["--thread", "thread-demo", "--json"]) == 0
    payload, _ = _json_stdout(capsys)
    assert fake.popen_calls[0]["argv"][-1] == tab_launch.URI_BASE
    assert "prompt" not in payload["uri"]
    assert payload["thread_id"] == "thread-demo"


# ------------------------------------------------------------------ detection

def test_detects_the_new_session(harness, capsys):
    # snapshot, poll at t=0, poll at t=2, poll at t=4 (it appears)
    fake = harness.subprocess([BASE, BASE, BASE, BASE + [NEW]])

    rc = tab_launch.main(["--thread", "thread-demo", "--json"])

    payload, _ = _json_stdout(capsys)
    assert rc == 0
    assert set(payload) == OUTPUT_KEYS
    assert payload == {"session_id": NEW["sessionId"], "native_name": "myproject-b3",
                       "status": "idle", "elapsed_s": 4.0, "thread_id": "thread-demo",
                       "uri": tab_launch.URI_BASE}
    assert harness.clock.sleeps == [2.0, 2.0]
    assert len(fake.popen_calls) == 1
    # The snapshot is taken BEFORE launching: otherwise the new tab would fall inside the snapshot.
    assert fake.events[:2] == ["run", "popen"]


def test_human_output_without_json(harness, capsys):
    harness.subprocess([BASE, BASE + [NEW]])
    assert tab_launch.main(["--thread", "thread-demo"]) == 0
    out = capsys.readouterr().out
    assert NEW["sessionId"] in out and "myproject-b3" in out and "thread-demo" in out


def test_accepts_object_with_agents_key(harness, capsys):
    harness.subprocess([{"agents": BASE}, {"agents": BASE + [NEW]}])
    assert tab_launch.main(["--json"]) == 0
    payload, _ = _json_stdout(capsys)
    assert payload["session_id"] == NEW["sessionId"]


def test_new_non_interactive_session_does_not_count(harness, capsys):
    background = _agent("dddddddd-0000-4000-8000-00000000000d", "worker-x", kind="background")
    harness.subprocess([BASE, BASE + [background], BASE + [background, NEW]])
    assert tab_launch.main(["--json"]) == 0
    payload, _ = _json_stdout(capsys)
    assert payload["session_id"] == NEW["sessionId"]


def test_session_already_in_the_snapshot_does_not_count(harness, capsys):
    harness.subprocess([BASE + [NEW]])  # it was already alive before the launch
    assert tab_launch.main(["--timeout", "4", "--json"]) == 2


def test_transient_failed_poll_does_not_cut_the_wait(harness, capsys):
    harness.subprocess([BASE, (1, "", "transient boom"), BASE + [NEW]])
    assert tab_launch.main(["--json"]) == 0
    payload, err = _json_stdout(capsys)
    assert payload["session_id"] == NEW["sessionId"]
    assert "transient boom" in err  # the failure leaves a trace (rule 16)


def test_code_exe_that_does_not_finish_is_not_killed_and_polling_continues(harness, capsys):
    harness.subprocess([BASE, BASE + [NEW]], code_exit=None)
    assert tab_launch.main(["--json"]) == 0
    payload, err = _json_stdout(capsys)
    assert payload["session_id"] == NEW["sessionId"]
    assert "did not finish" in err


# ------------------------------------------------------------------ timeout

def test_timeout_returns_exit_2_and_names_the_dialog(harness, capsys):
    fake = harness.subprocess([BASE])

    rc = tab_launch.main(["--timeout", "10", "--thread", "thread-demo", "--json"])

    payload, err = _json_stdout(capsys)
    assert rc == 2
    assert "VS Code dialog" in err and "Open" in err
    assert "VS Code dialog" in payload["error"]
    assert payload["session_id"] is None and payload["thread_id"] == "thread-demo"
    assert payload["elapsed_s"] == 10.0
    assert harness.clock.sleeps == [2.0] * 5          # polls at t=0,2,4,6,8,10
    assert len(fake.run_calls) == 1 + 6                # snapshot + 6 polls
    assert len(fake.popen_calls) == 1                  # a single launch, no retry
    assert harness.log_lines()[-1]["result"] == "timeout"


def test_timeout_not_a_multiple_of_the_poll_does_not_overshoot(harness, capsys):
    harness.subprocess([BASE])
    assert tab_launch.main(["--timeout", "5", "--json"]) == 2
    assert harness.clock.sleeps == [2.0, 2.0, 1.0]


def test_if_no_poll_answers_it_is_an_error_not_a_timeout(harness, capsys):
    harness.subprocess([BASE, (1, "", "broken claude")])
    rc = tab_launch.main(["--timeout", "4", "--json"])
    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert "broken claude" in payload["error"]
    assert "dialog" not in payload["error"]


# ------------------------------------------------------------------ two candidates

def test_two_candidates_returns_exit_1_and_lists_them(harness, capsys):
    harness.subprocess([BASE, BASE + [NEW, NEW_2]])

    rc = tab_launch.main(["--json"])

    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert payload["session_id"] is None, "it does not guess which one"
    assert [c["session_id"] for c in payload["candidates"]] == [NEW["sessionId"],
                                                               NEW_2["sessionId"]]
    assert [c["native_name"] for c in payload["candidates"]] == ["myproject-b3",
                                                                "myproject-c0"]
    for c in (NEW, NEW_2):
        assert c["sessionId"] in err and c["name"] in err
    assert harness.log_lines()[-1]["result"] == "ambiguous"


# ------------------------------------------------------------------ dry-run

def test_dry_run_launches_nothing(harness, monkeypatch, capsys):
    monkeypatch.setenv(VAR, "sentinel-value-xyz")
    fake = harness.subprocess([BASE, BASE + [NEW]])

    rc = tab_launch.main(["--dry-run", "--thread", "thread-demo", "--json"])

    out = capsys.readouterr().out
    plan = json.loads(out)
    assert rc == 0
    assert fake.events == [], "neither Code.exe nor claude agents"
    assert not harness.log.exists(), "the dry-run does not write the log"
    assert plan["dry_run"] is True
    assert plan["code_exe"] == str(harness.code_exe) and plan["code_exe_source"] == "registry"
    assert plan["argv"] == [str(harness.code_exe), "--open-url", "--", tab_launch.URI_BASE]
    assert plan["uri"] == tab_launch.URI_BASE
    assert plan["env_removed"] == [VAR] and plan["env_removed_present"] == [VAR]
    assert plan["thread_id"] == "thread-demo"
    assert "sentinel-value-xyz" not in out, "only the presence of the variable, never its value"


def test_human_dry_run_shows_executable_uri_and_variable(harness, monkeypatch, capsys):
    monkeypatch.delenv(VAR, raising=False)
    fake = harness.subprocess([BASE])
    assert tab_launch.main(["--dry-run", "--prompt", "hello world"]) == 0
    out = capsys.readouterr().out
    assert str(harness.code_exe) in out
    assert tab_launch.URI_BASE + "?prompt=hello%20world" in out
    assert VAR in out and "not in this environment" in out
    assert fake.events == []


# ------------------------------------------------------------------ Code.exe resolution

def test_exe_from_command():
    f = tab_launch.exe_from_command
    path = "C:\\Users\\x\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe"
    assert f(f'"{path}" --open-url -- "%1"') == path
    assert f(f'{path} --open-url -- "%1"') == path      # unquoted and with spaces
    assert f("C:\\vsc\\CODE.EXE") == "C:\\vsc\\CODE.EXE"
    assert f("") is None and f(None) is None and f('"') is None


def test_forced_code_exe_beats_the_registry(harness, tmp_path):
    other = tmp_path / "other" / "Code.exe"
    other.parent.mkdir()
    other.write_bytes(b"")
    assert tab_launch.resolve_code_exe(str(other)) == (str(other), "--code-exe")


def test_nonexistent_forced_code_exe_does_not_fall_back_to_the_registry(harness, tmp_path):
    with pytest.raises(tab_launch.TabLaunchError, match="does not exist"):
        tab_launch.resolve_code_exe(str(tmp_path / "nothing" / "Code.exe"))


def test_forced_code_exe_rejects_cmd_wrapper(harness, tmp_path):
    wrapper = tmp_path / "code.cmd"
    wrapper.write_text("@echo off\n", encoding="utf-8")
    with pytest.raises(tab_launch.TabLaunchError, match="wrapper"):
        tab_launch.resolve_code_exe(str(wrapper))


def test_code_exe_comes_from_the_registry(harness):
    assert tab_launch.resolve_code_exe() == (str(harness.code_exe), "registry")


def test_without_registry_it_uses_the_last_resort(harness, monkeypatch, tmp_path):
    backup = tmp_path / "backup" / "Code.exe"
    backup.parent.mkdir()
    backup.write_bytes(b"")
    monkeypatch.setattr(tab_launch, "read_registry_command", lambda: None)
    monkeypatch.setattr(tab_launch, "last_resort_candidates",
                        lambda: [str(tmp_path / "no" / "Code.exe"), str(backup)])
    assert tab_launch.resolve_code_exe() == (str(backup), "last-resort")


def test_registry_pointing_to_a_deleted_exe_falls_back_to_the_last_resort(harness, monkeypatch,
                                                                         tmp_path, capsys):
    backup = tmp_path / "backup" / "Code.exe"
    backup.parent.mkdir()
    backup.write_bytes(b"")
    monkeypatch.setattr(tab_launch, "read_registry_command",
                        lambda: '"C:\\gone\\no\\longer\\Code.exe" --open-url -- "%1"')
    monkeypatch.setattr(tab_launch, "last_resort_candidates", lambda: [str(backup)])
    assert tab_launch.resolve_code_exe() == (str(backup), "last-resort")
    assert "registry" in capsys.readouterr().err  # the discard leaves a trace


def test_last_resort_includes_the_known_path(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "D:\\other\\Local")
    paths = tab_launch.last_resort_candidates()
    assert paths[0] == os.path.join("D:\\other\\Local", "Programs", "Microsoft VS Code",
                                    "Code.exe")
    assert paths[-1] == tab_launch.CODE_EXE_LAST_RESORT


def test_code_exe_not_found_returns_exit_1_without_launching(harness, monkeypatch, capsys):
    monkeypatch.setattr(tab_launch, "read_registry_command", lambda: None)
    monkeypatch.setattr(tab_launch, "last_resort_candidates", lambda: ["C:\\no\\Code.exe"])
    rc = tab_launch.main(["--json"])
    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert "Code.exe" in payload["error"] and "--code-exe" in payload["error"]
    assert harness.fake.events == []


# ------------------------------------------------------------------ other errors

def test_code_exe_with_nonzero_exit_returns_exit_1(harness, capsys):
    fake = harness.subprocess([BASE, BASE + [NEW]], code_exit=9,
                              code_output="Code.exe: bad option: --open-url\n")
    rc = tab_launch.main(["--json"])
    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert "exit 9" in payload["error"] and "bad option: --open-url" in payload["error"]
    assert len(fake.run_calls) == 1, "only the snapshot: with the URI unopened there is nothing to poll"
    last = harness.log_lines()[-1]
    assert last["result"] == "error" and last["launched"] is False


def test_code_exe_that_cannot_be_run_returns_exit_1(harness, capsys):
    harness.subprocess([BASE], popen_error=OSError(5, "Access denied"))
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "could not run" in payload["error"]


def test_claude_not_found_returns_exit_1_without_launching(harness, capsys):
    harness.claude(None)
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "claude" in payload["error"]
    assert harness.fake.events == []


def test_outside_windows_returns_exit_1_without_launching(harness, monkeypatch, capsys):
    monkeypatch.setattr(tab_launch, "is_windows", lambda: False)
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "Windows" in payload["error"]
    assert harness.fake.events == []


def test_if_the_initial_snapshot_fails_nothing_is_launched(harness, capsys):
    fake = harness.subprocess([(1, "", "no session")])
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "no session" in payload["error"]
    assert fake.popen_calls == [], "without a snapshot the new tab cannot be told apart"


def test_invalid_json_from_claude_agents_is_an_error(harness, capsys):
    fake = harness.subprocess(["this is not json"])
    assert tab_launch.main(["--json"]) == 1
    assert fake.popen_calls == []


def test_usage_error_exits_with_1_not_2(harness, capsys):
    # argparse uses 2 for usage errors; here 2 is "timeout" and the Router would misread it.
    for argv in (["--timeout", "abc"], ["--timeout", "0"], ["--nonexistent-option"]):
        with pytest.raises(SystemExit) as exc:
            tab_launch.main(argv)
        assert exc.value.code == 1, argv
    assert harness.fake.events == []


# ------------------------------------------------------------------ log

def test_log_is_append_only_with_art_time_thread_uri_and_result(harness, capsys):
    harness.subprocess([BASE, BASE + [NEW]])
    assert tab_launch.main(["--thread", "thread-one", "--json"]) == 0
    first = harness.log_lines()
    assert [x["event"] for x in first] == ["launched", "result"]

    harness.subprocess([BASE])
    assert tab_launch.main(["--thread", "thread-two", "--timeout", "2", "--json"]) == 2
    everything = harness.log_lines()
    assert everything[:2] == first, "the old lines are not rewritten"
    assert [x["event"] for x in everything] == ["launched", "result", "launched", "result"]

    for line in everything:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ART", line["ts"])
        assert line["uri"] == tab_launch.URI_BASE
    assert [x["thread_id"] for x in everything] == ["thread-one"] * 2 + ["thread-two"] * 2
    ok, expired = everything[1], everything[3]
    assert ok["result"] == "ok" and ok["session_id"] == NEW["sessionId"]
    assert ok["native_name"] == "myproject-b3" and ok["exit_code"] == 0
    assert expired["result"] == "timeout" and expired["exit_code"] == 2


def test_log_that_cannot_be_written_does_not_cut_the_launch(harness, monkeypatch, capsys,
                                                           tmp_path):
    blocker = tmp_path / "i_am_a_file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(tab_launch, "LOG_PATH", blocker / "tab_launch.log")
    harness.subprocess([BASE, BASE + [NEW]])
    assert tab_launch.main(["--json"]) == 0
    payload, err = _json_stdout(capsys)
    assert payload["session_id"] == NEW["sessionId"]
    assert "could not write the log" in err


def test_art_time_comes_from_the_clock():
    from datetime import datetime, timedelta, timezone
    before = datetime.now(timezone.utc)
    stamp = tab_launch.now_art()
    assert stamp.endswith(" ART")
    read = datetime.strptime(stamp[:-4], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone(timedelta(hours=-3)))
    assert abs((read - before).total_seconds()) < 5
