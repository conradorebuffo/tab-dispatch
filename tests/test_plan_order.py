"""The `plan` order: the child writes a plan file, the human approves it, each step has its done."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ledger  # noqa: E402

OK = 'python -c "import sys; sys.exit(0)"'
BAD = 'python -c "import sys; sys.exit(3)"'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "home"))
    return tmp_path


def _plan_file(root, steps, name="001_add_search.md"):
    path = root / "plans" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Plan: add search", ""] + [f"- [ ] Step {n}: {what}  done: `{cmd}`" for n, what, cmd in steps]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _plan_order():
    oid = ledger.assign("add-search", "plan the search feature", OK, kind="plan", files=["plans/"])
    ledger.accept(oid, session_id="sess-child")
    return oid


def test_assign_takes_the_plan_kind_from_the_command_line(capsys):
    rc = ledger.main(["assign", "--kind", "plan", "--ref", "test-plan", "--objective", "x",
                      "--done", "exit 0", "--files", "a.md"])
    assert rc == 0
    assert "kind: plan" in capsys.readouterr().out


def test_register_approve_and_check_every_step(home):
    oid = _plan_order()
    path = _plan_file(home, [(1, "write the index", OK), (2, "wire the endpoint", OK)])
    assert ledger.plan_register(oid, path)["steps"] == 2
    assert ledger.get(oid)["approved_at"] is None
    ledger.plan_approve(oid)
    ok, results = ledger.plan_check(oid)
    assert ok and [r["exit_code"] for r in results] == [0, 0]


def test_a_failing_step_fails_the_plan_check(home):
    oid = _plan_order()
    ledger.plan_register(oid, _plan_file(home, [(1, "passes", OK), (2, "fails", BAD)]))
    ledger.plan_approve(oid)
    ok, results = ledger.plan_check(oid)
    assert not ok and results[1]["exit_code"] == 3
    ok_one, one = ledger.plan_check(oid, step=1)
    assert ok_one and len(one) == 1


def test_no_execution_before_the_plan_is_approved(home):
    oid = _plan_order()
    ledger.plan_register(oid, _plan_file(home, [(1, "x", OK)]))
    with pytest.raises(ledger.LedgerError) as err:
        ledger.plan_check(oid)
    assert err.value.code == "plan_not_approved"


def test_register_rejects_what_is_not_a_plan(home):
    work = ledger.assign("w", "work", OK)
    ledger.accept(work)
    good = _plan_file(home, [(1, "x", OK)])
    with pytest.raises(ledger.LedgerError) as err:
        ledger.plan_register(work, good)
    assert err.value.code == "not_a_plan_order"
    oid = _plan_order()
    wrong_name = home / "plans" / "search.md"
    wrong_name.write_text("- [ ] Step 1: x  done: `exit 0`\n", encoding="utf-8")
    empty = home / "plans" / "002_empty.md"
    empty.write_text("# no steps here\n", encoding="utf-8")
    for path, code in ((wrong_name, "bad_plan_path"), (home / "plans" / "003_missing.md", "no_plan_file"),
                       (empty, "plan_without_steps")):
        with pytest.raises(ledger.LedgerError) as err:
            ledger.plan_register(oid, path)
        assert err.value.code == code


def test_a_registered_plan_waits_in_the_attention_queue_until_approved(home):
    oid = _plan_order()
    ledger.plan_register(oid, _plan_file(home, [(1, "x", OK)]))
    items = ledger.attention()
    assert [i["type"] for i in items] == ["plan_to_approve"] and items[0]["id"] == f"r-{oid}"
    ledger.plan_approve(oid)
    assert ledger.attention() == []


def test_plan_commands_from_the_command_line(home, capsys):
    oid = _plan_order()
    path = _plan_file(home, [(1, "x", OK)])
    assert ledger.main(["plan", "register", str(oid), "--path", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["steps"] == 1
    assert ledger.main(["plan", "check", str(oid)]) == 2  # not approved: a contract error
    capsys.readouterr()
    assert ledger.main(["plan", "approve", str(oid)]) == 0
    capsys.readouterr()
    assert ledger.main(["plan", "check", str(oid)]) == 0
