import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import done_criterio as dc  # noqa: E402


def test_exit_zero_is_verified():
    ok, d = dc.check_done("r-1", 'python -c "print(1)"')
    assert ok and d["exit_code"] == 0 and d["stdout"] == "1"


def test_nonzero_exit_is_not_verified_and_keeps_the_reason():
    ok, d = dc.check_done("r-1", 'python -c "import sys; sys.stderr.write(\'boom\'); sys.exit(4)"')
    assert not ok and d["exit_code"] == 4 and "boom" in d["reason"]


def test_timeout_fails_closed(monkeypatch):
    monkeypatch.setattr(dc, "TIMEOUT_S", 1)
    ok, d = dc.check_done("r-1", 'python -c "import time; time.sleep(5)"')
    assert not ok and d["exit_code"] is None and "did not finish" in d["reason"]


def test_cli_exit_codes(capsys):
    assert dc.main(["--check", "r-1", 'python -c "pass"']) == 0
    assert "VERIFIED" in capsys.readouterr().out
    assert dc.main(["--check", "r-1", 'python -c "raise SystemExit(1)"']) == 1
