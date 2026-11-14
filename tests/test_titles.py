"""Tab names of 0.2: S<n>-<TitleInCamelCase>, with <n> counted in the plugin's own ledger."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ledger  # noqa: E402
import titles  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path))
    return tmp_path


def test_canonical_name_drops_filler_words_and_joins_in_camel_case():
    assert titles.canonical_name(42, "State of the art report") == "S42-StateArtReport"


def test_canonical_name_keeps_a_name_that_already_has_its_prefix():
    assert titles.canonical_name(7, "S7-PythonTests") == "S7-PythonTests"
    assert titles.canonical_name(8, "S7-PythonTests") == "S8-S7PythonTests"


def test_canonical_name_takes_at_most_four_words_and_keeps_capitals():
    assert titles.canonical_name(3, "API gateway rate limit tests extra") == "S3-APIGatewayRateLimit"


def test_canonical_name_strips_accents_and_punctuation():
    accented = "caf" + chr(0xE9) + " men" + chr(0xFA) + ": v2!"
    assert titles.canonical_name(4, accented) == "S4-CafeMenuV2"


def test_canonical_name_without_usable_words():
    assert titles.canonical_name(5, "") == "S5-Session"
    assert titles.canonical_name(5, None) == "S5-Session"
    assert titles.canonical_name(6, "the of") == "S6-TheOf"


def test_session_number_counts_sessions_in_the_ledger():
    assert ledger.session_number("sess-a") == 1
    assert ledger.session_number("sess-b") == 2
    assert ledger.session_number("sess-a") == 1


def test_title_of_the_router_and_of_a_child():
    ledger.set_role("sess-router", "coord", "release check")
    ledger.set_role("sess-child", "child", "python tests")
    assert titles.title_for_session("sess-router") == "S1-Router"
    assert titles.title_for_session("sess-child") == "S2-PythonTests"


def test_title_stays_the_same_when_the_child_takes_an_order():
    ledger.set_role("sess-child", "child", "node tests")
    before = titles.title_for_session("sess-child")
    oid = ledger.assign("r1", "run the tests", "python -c pass")
    ledger.accept(oid, session_id="sess-child")
    assert titles.title_for_session("sess-child") == before == "S1-NodeTests"


def test_a_session_without_a_role_has_no_title_and_takes_no_number():
    assert titles.title_for_session("sess-nobody") is None
    assert ledger.session_number("sess-first") == 1


def test_compute_title_without_a_number_keeps_the_old_form():
    assert titles.compute_title({"role": "child", "scope": "ledger"}, 7) == "r-7 ledger"
    assert titles.compute_title({"role": "child", "scope": None}, number=9) == "S9-Child"


def test_session_numbers_have_no_gaps():
    # Run of 2026-11-14: five tabs were named S1, S2, S3, S7 and S14.
    for _ in range(4):
        assert ledger.session_number("sess-a") == 1
    assert ledger.session_number("sess-b") == 2
    for _ in range(3):
        ledger.session_number("sess-a"), ledger.session_number("sess-b")
    assert ledger.session_number("sess-c") == 3


def test_a_role_declared_before_the_first_message_is_announced_with_that_message():
    # Run of 2026-11-14: four child tabs kept the label "Claude Code". The title was on disk,
    # and nothing handed it to Claude Code while the tab was open.
    ledger.set_role("sess-router", "coord")
    ledger.set_role("sess-child", "child", "ticket order")
    out = titles.announce("sess-child", "UserPromptSubmit")
    assert out == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": "S2-TicketOrder"}}


def test_a_title_is_announced_once_per_title_on_a_message():
    ledger.set_role("sess-child", "child", "ticket order")
    assert titles.announce("sess-child", "UserPromptSubmit") is not None
    assert titles.announce("sess-child", "UserPromptSubmit") is None
    ledger.set_role("sess-child", "child", "docs")
    assert titles.announce("sess-child", "UserPromptSubmit")["hookSpecificOutput"]["sessionTitle"] == "S1-Docs"


def test_a_session_start_always_announces():
    ledger.set_role("sess-child", "child", "docs")
    for _ in range(2):
        out = titles.announce("sess-child", "SessionStart")
        assert out["hookSpecificOutput"] == {"hookEventName": "SessionStart", "sessionTitle": "S1-Docs"}


def test_nothing_is_announced_without_a_role_or_for_another_event():
    assert titles.announce("sess-none", "UserPromptSubmit") is None
    ledger.set_role("sess-child", "child", "docs")
    assert titles.announce("sess-child", "Stop") is None
    assert titles.announce("sess-child", None) is None
    assert titles.announce(None, "SessionStart") is None
    assert titles.announce("../outside", "SessionStart") is None
