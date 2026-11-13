"""prompt_count: counts per tab, by sender, and never a word of the messages themselves."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ledger  # noqa: E402
import prompt_count  # noqa: E402

COORD = "11111111-aaaa-4bbb-8ccc-000000000001"
CHILD = "22222222-aaaa-4bbb-8ccc-000000000002"
# Every message below carries one of these; none may ever reach the output.
SECRETS = ("PLUM-7731", "QUINCE-4410", "ORCHID-0912", "TULIP-5521", "MAPLE-3307", "CEDAR-8164",
           "BIRCH-2290", "LOTUS-6618", "OLIVE-1043", "AMBER-7756", "SAGE-3389", "IVORY-4402")
PIPE = "uds:\\\\.\\pipe\\LOCAL\\cc-msg-demo"


def _at(minute):
    return f"2026-11-13T21:{minute:02d}:00.000Z"


def _user(minute, content, **extra):
    return {"type": "user", "timestamp": _at(minute), "message": {"role": "user", "content": content}, **extra}


def _peer(name, body, frm=PIPE):
    return {"kind": "peer", "from": frm, "name": name, "body": body}


def _coord_lines():
    report = ('<cross-session-message from="x" from-name="S2-Child">\n[2026-11-13 18:10 -0300] [REPORT r-1]\n'
              f"state: ready\nevidence: {SECRETS[3]}\n</cross-session-message>")
    return [
        _user(0, f"fix the login page {SECRETS[0]}", origin={"kind": "human"}),
        _user(1, f"<command-name>/review</command-name> {SECRETS[1]}", turnOrigin="human"),
        {"type": "attachment", "timestamp": _at(2),
         "attachment": {"type": "queued_command", "prompt": f"also {SECRETS[2]}", "origin": {"kind": "human"}}},
        _user(3, report, origin=_peer("S2-Child", SECRETS[3]), turnOrigin="peer", isMeta=True),
        _user(4, f"[{SECRETS[4]}] hello there", origin=_peer("S2-Child", SECRETS[4]), turnOrigin="peer"),
        _user(5, f"subagent result {SECRETS[5]}", origin=_peer("worker", SECRETS[5], frm="agent:abc")),
        _user(6, f"<task-notification>{SECRETS[6]}</task-notification>", origin={"kind": "task-notification"}),
        _user(7, f"skill body {SECRETS[7]}", isMeta=True),
        _user(8, f"This session is being continued from a previous conversation. {SECRETS[8]}"),
        _user(9, "[Request interrupted by user]"),
        {"type": "assistant", "timestamp": _at(10), "message": {"content": [
            {"type": "tool_use", "id": "ask1", "name": "AskUserQuestion", "input": {}}]}},
        _user(11, [{"type": "tool_result", "tool_use_id": "ask1", "content": f"picked {SECRETS[9]}"}]),
        {"type": "assistant", "timestamp": _at(12), "message": {"content": [
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]}},
        _user(13, [{"type": "tool_result", "tool_use_id": "b1", "is_error": True,
                    "content": f"The user doesn't want to proceed with this tool use. {SECRETS[10]}"}]),
        _user(14, f"typed in a side chain {SECRETS[11]}", origin={"kind": "human"}, isSidechain=True),
        _user(15, f"odd line {SECRETS[11]}"),
    ]


def _child_lines():
    order = f"[2026-11-13 18:05 -0300] [ORDER r-1]\nref: demo\nobjective: {SECRETS[0]}"
    return [
        _user(5, order, origin=_peer("S1-Router", order), turnOrigin="peer", isMeta=True),
        {"type": "attachment", "timestamp": _at(6),
         "attachment": {"type": "queued_command", "prompt": f"[2026-11-13 18:06 -0300] [NOTE] {SECRETS[1]}",
                        "origin": _peer("S1-Router", SECRETS[1])}},
        _user(7, [{"type": "tool_result", "tool_use_id": "t1", "content": SECRETS[2]}]),
    ]


def _write(path, lines, extra_raw=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
        for raw in extra_raw:
            f.write(raw + "\n")
    return path


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A ledger with a Router and a child tab, and a transcript for each under the projects folder."""
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "home"))
    projects = tmp_path / "claude-config" / "projects" / "demo"
    coord = _write(projects / f"{COORD}.jsonl", _coord_lines(), extra_raw=["{not json"])
    child = _write(projects / f"{CHILD}.jsonl", _child_lines())
    ledger.set_role(COORD, "coord")
    ledger.set_role(CHILD, "child", "demo work")
    return {"coord": coord, "child": child, "root": tmp_path}


def _run(world, *args):
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(TAB_DISPATCH_HOME=str(world["root"] / "home"),
               CLAUDE_CONFIG_DIR=str(world["root"] / "claude-config"), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(SCRIPTS / "prompt_count.py"), *args], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _snapshot(root):
    return {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_counts_every_class_in_one_transcript(world):
    res = prompt_count.count_transcript(world["coord"])
    assert res["counts"] == {"typed": 3, "from_tab": 2, "meta": 4, "dialogs": 3, "unclassified": 1}
    assert res["subclasses"]["typed:slash_command"] == 1
    assert res["subclasses"]["from_tab:[REPORT:ready]"] == 1
    assert res["subclasses"]["from_tab:free_text"] == 1
    assert res["dialogs"] == {"ask_user_question_answered": 1, "interrupt": 1, "tool_rejected": 1}
    assert res["senders"] == {"S2-Child": 2}
    assert res["unreadable_lines"] == 1


def test_markers_come_only_from_the_closed_list():
    assert prompt_count._marker("[2026-11-13 18:05 -0300] [ORDER r-9]\nref: x") == "[ORDER]"
    assert prompt_count._marker("[2026-11-13 18:05 ART] [REPORT r-9]\nstate: waiting") == "[REPORT:waiting]"
    assert prompt_count._marker("[SECRET-MARK r-9] anything") == "free_text"


def test_tabs_come_from_the_ledger_and_the_summary_splits_typed_by_role(world):
    out = _run(world, "--json")
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    by_role = {t["role"]: t for t in result["tabs"]}
    assert by_role["coord"]["counts"]["typed"] == 3
    assert by_role["child"]["counts"] == {"typed": 0, "from_tab": 2, "meta": 0, "dialogs": 0, "unclassified": 0}
    assert by_role["child"]["subclasses"] == {"from_tab:[NOTE]": 1, "from_tab:[ORDER]": 1}
    assert result["summary"] == {"tabs_counted": 2, "typed_total": 3, "typed_in_coord": 3, "typed_in_other_tabs": 0}


@pytest.mark.parametrize("fmt", [(), ("--json",)])
def test_output_never_contains_message_text(world, fmt):
    out = _run(world, *fmt)
    assert out.returncode == 0, out.stderr
    printed = out.stdout + out.stderr
    assert "Typed by the human" in printed or '"typed_total"' in printed
    leaked = [s for s in SECRETS if s in printed]
    assert not leaked, f"message text reached the output: {leaked}"
    for word in ("login page", "hello there", "skill body", "subagent result", "odd line"):
        assert word not in printed


def test_counting_writes_nothing(world):
    before = _snapshot(world["root"])
    assert _run(world, "--json").returncode == 0
    assert _snapshot(world["root"]) == before


def test_since_and_until_keep_a_window(world):
    res = prompt_count.count_transcript(world["coord"], since=prompt_count._moment("2026-11-13T21:01:30+00:00"),
                                        until=prompt_count._moment("2026-11-13T21:04:30+00:00"))
    assert res["counts"]["typed"] == 1          # only the queued one, at 21:02
    assert res["counts"]["from_tab"] == 2
    assert res["outside_window"]["typed"] == 2


def test_explicit_transcript_without_a_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "empty"))
    path = _write(tmp_path / "loose" / f"{CHILD}.jsonl", _child_lines())
    assert prompt_count.main(["--transcript", str(path), "--json"]) == 0


def test_no_ledger_and_no_tab_named_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "empty"))
    assert prompt_count.main([]) == 2
    assert "no ledger" in capsys.readouterr().err


def test_a_tab_without_transcript_is_reported_and_exits_1(world):
    world["child"].unlink()
    out = _run(world, "--json")
    assert out.returncode == 1
    child = next(t for t in json.loads(out.stdout)["tabs"] if t["role"] == "child")
    assert child["error"] == "transcript not found"
