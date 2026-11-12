import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import env_check as ec  # noqa: E402


def test_exact_minimums_pass():
    assert ec.check("2.1.321 (Claude Code)", "2.1.322", "win32") == []


def test_newer_versions_pass():
    assert ec.check("2.2.0", "3.0.1", "win32") == []


@pytest.mark.parametrize("cc,ext,platform,needle", [
    ("2.1.320", "2.1.322", "win32", "Claude Code 2.1.320 is older"),
    ("2.1.321", "2.1.321", "win32", "extension 2.1.321 is older"),
    ("2.1.321", "2.1.322", "linux", "untested"),
    ("", "2.1.322", "win32", "Claude Code version not found"),
    ("2.1.321", "", "win32", "extension anthropic.claude-code not found"),
])
def test_each_unmet_requirement_is_named(cc, ext, platform, needle):
    problems = ec.check(cc, ext, platform)
    assert len(problems) == 1 and needle in problems[0]


def test_numeric_not_lexical_comparison():
    assert ec.check("2.1.1000", "2.1.322", "win32") == []
    assert ec.check("2.1.99", "2.1.322", "win32")


def test_cli_exit_codes(capsys):
    assert ec.main(["--claude-code", "2.1.321", "--extension", "2.1.322", "--platform", "win32"]) == 0
    assert ec.main(["--claude-code", "2.0.0", "--extension", "2.1.322", "--platform", "win32"]) == 1
    assert "PROBLEM" in capsys.readouterr().out
