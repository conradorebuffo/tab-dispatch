"""Threads: a piece of work parked in threads/<id>.md, to continue, pause or finish."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ledger  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAB_DISPATCH_HOME", str(tmp_path / "home"))
    return tmp_path


def _park(home, outcome="pause", **kw):
    args = dict(thread_id="search-feature", outcome=outcome, context="adding search to the help desk",
                done="index written", next_step="wire the endpoint", session_id="sess-1", root=home)
    args.update(kw)
    return ledger.thread_park(**args)


def test_pause_writes_the_thread_file_with_its_three_parts(home):
    row = _park(home)
    text = (home / "threads" / "search-feature.md").read_text(encoding="utf-8")
    assert row["status"] == "paused"
    for part in ("status: paused", "## Context", "adding search", "## Done", "index written",
                 "## Next step", "wire the endpoint", "## Log", "pause by session sess-1"):
        assert part in text


def test_a_new_session_resumes_a_paused_thread(home):
    _park(home)
    assert [t["id"] for t in ledger.list_threads("paused")] == ["search-feature"]
    got = ledger.thread_resume("search-feature", session_id="sess-2")
    assert got["status"] == "active" and got["last_session"] == "sess-2"
    assert "wire the endpoint" in got["text"] and "status: active" in got["text"]
    assert "resumed by session sess-2" in got["text"]
    assert ledger.list_threads("paused") == []


def test_a_paused_thread_waits_in_the_attention_queue(home):
    _park(home)
    items = ledger.attention()
    assert [(i["type"], i["id"], i["what"]) for i in items] == [("paused_thread", "search-feature", "wire the endpoint")]
    ledger.thread_resume("search-feature", session_id="sess-2")
    assert ledger.attention() == []


def test_continue_keeps_it_active_and_done_closes_it(home):
    assert _park(home, outcome="continue")["status"] == "active"
    assert _park(home, outcome="done", next_step="")["status"] == "done"
    with pytest.raises(ledger.LedgerError) as err:
        ledger.thread_resume("search-feature")
    assert err.value.code == "thread_done"


def test_the_log_keeps_every_park_and_the_file_stays_in_place(home):
    _park(home)
    ledger.thread_resume("search-feature", session_id="sess-2")
    _park(home, done="endpoint wired", next_step="write the docs", session_id="sess-2", root=home / "elsewhere")
    text = (home / "threads" / "search-feature.md").read_text(encoding="utf-8")
    assert text.count("pause by session") == 2 and "resumed by session sess-2" in text
    assert "endpoint wired" in text and not (home / "elsewhere").exists()


def test_what_a_thread_needs(home):
    for kw, code in ((dict(thread_id="Bad Id"), "bad_thread_id"), (dict(outcome="later"), "bad_outcome"),
                     (dict(next_step=""), "empty_field"), (dict(context=""), "empty_field")):
        with pytest.raises(ledger.LedgerError) as err:
            _park(home, **kw)
        assert err.value.code == code
    with pytest.raises(ledger.LedgerError) as err:
        ledger.thread_resume("no-such-thread")
    assert err.value.code == "unknown_thread"


def test_thread_commands_from_the_command_line(home, monkeypatch, capsys):
    monkeypatch.chdir(home)
    assert ledger.main(["thread", "park", "docs", "--outcome", "pause", "--context", "the user guide",
                        "--next", "write chapter 2"]) == 0
    assert (home / "threads" / "docs.md").is_file()
    capsys.readouterr()
    assert ledger.main(["thread", "resume", "docs"]) == 0
    assert "write chapter 2" in capsys.readouterr().out
    assert ledger.main(["thread", "resume", "nope"]) == 2
