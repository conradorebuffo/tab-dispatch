"""The report contract: what the ledger prints, the parser accepts; anything else is refused."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ledger  # noqa: E402
import report_contract as rc  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path))


def _accepted():
    oid = ledger.assign("r", "o", "exit 0")
    ledger.accept(oid)
    return oid


def test_ledger_report_roundtrips_through_the_parser():
    oid = _accepted()
    parsed = rc.parse_report(ledger.report(oid, "ready", "abc1234; 5 passed"))
    assert parsed == {"id": oid, "state": "ready", "evidence": "abc1234; 5 passed", "question": None}


def test_waiting_report_carries_its_question():
    oid = _accepted()
    parsed = rc.parse_report(ledger.report(oid, "waiting", "blocked on repo", "create the repo?"))
    assert parsed["question"] == "create the repo?"


def test_ledger_order_roundtrips_through_the_parser():
    oid = ledger.assign("ref-1", "build it", "exit 0", limits="only docs/")
    parsed = rc.parse_order(ledger.render_order(oid))
    assert parsed["id"] == oid and parsed["limits"] == "only docs/"


@pytest.mark.parametrize("block", [
    "",
    "just text",
    "[2026-11-12 10:00 -0300] [REPORT r-1]\nstate: progress\nevidence: e",
    "[2026-11-12 10:00 -0300] [REPORT r-1]\nstate: ready",
    "[2026-11-12 10:00 -0300] [REPORT r-1]\nstate: waiting\nevidence: e",
    "[2026-11-12 10:00 -0300] [REPORT r-1]\nstate: ready\nevidence: e\nquestion: q",
    "[2026-11-12 10:00 -0300] [REPORT r-1]\nstate: ready\nevidence: e\nstate: failed",
    "[2026-11-12 10:00 -0300] [ORDER r-1]\nstate: ready\nevidence: e",
])
def test_bad_reports_are_refused(block):
    with pytest.raises(rc.ContractError):
        rc.parse_report(block)


def test_report_over_600_characters_is_refused():
    block = "[2026-11-12 10:00 -0300] [REPORT r-1]\nstate: ready\nevidence: " + "x" * 600
    with pytest.raises(rc.ContractError):
        rc.parse_report(block)


def test_order_missing_a_field_is_refused():
    with pytest.raises(rc.ContractError):
        rc.parse_order("[2026-11-12 10:00 -0300] [ORDER r-1]\nref: r\nobjective: o")
