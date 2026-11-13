"""The attention queue: what waits for the human, derived from the ledger, one item at a time."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ledger  # noqa: E402

OK = 'python -c "import sys; sys.exit(0)"'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "home"))
    return tmp_path


def _accepted(ref):
    oid = ledger.assign(ref, "do it", OK)
    ledger.accept(oid)
    return oid


def test_an_empty_ledger_asks_nothing(capsys):
    assert ledger.attention() == []
    assert ledger.main(["attention"]) == 0
    assert "Nothing waits" in capsys.readouterr().out


def test_a_waiting_order_shows_its_question():
    oid = _accepted("pick-db")
    ledger.report(oid, "waiting", "two options found", "SQLite or Postgres?")
    (item,) = ledger.attention()
    assert item["type"] == "question" and item["id"] == f"r-{oid}" and item["what"] == "SQLite or Postgres?"
    assert f"accept {oid}" in item["resolve"]


def test_blocked_and_failed_orders_are_listed_and_closed_ones_are_not():
    blocked, failed, fine = _accepted("a"), _accepted("b"), _accepted("c")
    ledger.report(blocked, "blocked", "the tool was denied")
    ledger.report(failed, "failed", "tests red: 2 failed")
    ledger.report(fine, "ready", "tests green")
    ledger.close(fine)
    assert sorted(i["type"] for i in ledger.attention()) == ["blocked", "failed"]


def test_an_answered_question_leaves_the_queue():
    oid = _accepted("pick-db")
    ledger.report(oid, "waiting", "two options", "which one?")
    ledger.accept(oid)
    assert ledger.attention() == []


def test_text_output_shows_one_item_and_the_count(capsys):
    for ref in ("one", "two", "three"):
        ledger.report(_accepted(ref), "waiting", "evidence", f"question {ref}?")
    assert ledger.main(["attention"]) == 0
    out = capsys.readouterr().out
    assert "[1 of 3]" in out and "question one?" in out and "question two?" not in out
    assert ledger.main(["attention", "--all"]) == 0
    assert "[3 of 3]" in capsys.readouterr().out


def test_json_output_has_every_item(capsys):
    ledger.report(_accepted("one"), "waiting", "evidence", "question one?")
    assert ledger.main(["attention", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1 and data["items"][0]["type"] == "question"
