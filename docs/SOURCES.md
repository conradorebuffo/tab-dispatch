# Sources of the figures in the README

| Figure or claim | Source |
|---|---|
| Order at most 1500 characters, report at most 600 | `scripts/ledger.py` (`ORDER_MAX`, `REPORT_MAX`), enforced by `tests/test_ledger.py` |
| Report states `ready`, `waiting`, `blocked`, `failed`, `dropped` | `scripts/ledger.py` (`REPORT_STATES`) and `scripts/report_contract.py` |
| Claude Code 2.1.321 or later, extension 2.1.322 or later | `scripts/env_check.py` (`MIN_CLAUDE_CODE`, `MIN_EXTENSION`), measured by the author on 2026-11-11 |
| Windows only | Only platform the author tested; `scripts/env_check.py` reports any other as untested |
| Tab title cut to 24 characters, `viewType` not documented | Measured by the author on 2026-11-11; the exact-title match in `extension/lib/match.js` and its tests in `extension/test/match.test.js` |
| Example order r-303 and its report (97 passed, 17 passed) | Real ledger blocks from the build of this plugin on 2026-11-12, trimmed; test counts from `python -m pytest tests -q` (97 passed) and `node --test extension/test/` (17 passed) at commit `bab0898` |
| Notes timestamped on 2026-11-11 by DigiCert and Sectigo (RFC 3161) | The author's `.tsr` timestamp tokens, verifiable with `openssl ts -verify`; published with the chapter |
| The VSIX installs without a dialog | Run on 2026-11-12 with `code --install-extension` in an isolated VS Code (`--user-data-dir`, `--extensions-dir`): exit 0, "successfully installed" |
