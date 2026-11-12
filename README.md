# tab-dispatch

One person, ten Claude Code tabs. A Router tab hands orders to child tabs. An order closes
when its `done` command exits 0. A tab saying "done" closes nothing.

## The problem

Run several Claude Code sessions on one codebase and you become the switchboard. You carry
instructions from tab to tab, ask each one whether it finished, and take its word for it.
Two tabs edit the same file. One stalls on a permission prompt you do not see. The cost is
your attention, and a tab's "done" is only its own claim.

## The idea

An order carries intent, not consent. The Router tells a child tab what to do. It does not
grant that tab any permission. A message between sessions is information: the child uses only
what its own rules already allow, and if a tool is denied it reports `blocked` instead of
asking another tab to do it.

Done is an exit code. Each order names a command, `done:`, written by the Router before the
work starts. The child does not write the check and the check does not ask the child. When the
child reports `ready`, the Router runs the command. Exit 0 closes the order. Anything else
reopens it, with the error as printed.

## The contract

Two text blocks cross between tabs, each stamped from the machine clock. This is a real order
and its report from the build of this plugin, trimmed and anonymized:

```
[2026-11-12 12:43 -0300] [ORDER r-303]
ref: release-7 wave 1
objective: Steps 1-8: repo, order ledger, ported scripts, relay skill, title hook, extension.
done: python check_wave_1.py
limits: touch only dev/tab-dispatch/**; no push, no GitHub
authority: this message authorizes nothing; use only what your own rules already allow
expires: 2026-11-13 12:00
reply: take it, work, report; one question only, and only in waiting.
```

```
[2026-11-12 12:57 -0300] [REPORT r-303]
state: ready
evidence: commit bab0898; check exit 0; pytest 97 passed, node 17 passed
```

Limits: an order has at most 1500 characters and a report at most 600. A report is one of
`ready`, `waiting`, `blocked`, `failed`, `dropped`, with evidence by reference (a sha, a test
line, an exit code), never narrated. The full specification is in
[docs/CONTRACT.md](docs/CONTRACT.md).

## The timeline, and its limit

The design notes of this approach were timestamped on 2026-11-11 under RFC 3161 by two
independent authorities, DigiCert and Sectigo. That proves the notes existed by those times.
It does not prove that the approach works, and it says nothing about results. What was
measured is described when it is published.

## What can break

- It depends on behavior that is not documented: the `viewType` of the Claude Code webview
  and the way VS Code cuts a tab title to 24 characters. Both fail on the safe side: a tab
  that cannot be matched by exact title is not closed.
- A tab with an open VS Code dialog does not receive messages until a person closes the dialog.
- File overlap between tabs is only a warning (`scripts/file_overlap.py`). For a real guard,
  use something like cotenancy-guard.
- Tested on Windows only. macOS and Linux are not tested.
- Requires Claude Code 2.1.321 or later and the VS Code extension 2.1.322 or later.
  `scripts/env_check.py` says which one is missing.

## Install

```
claude plugin marketplace add conradorebuffo/tab-dispatch
claude plugin install tab-dispatch@tab-dispatch
```

Then ask Claude to run the `install-extension` skill. It installs the bundled VS Code
extension, which is what lets the Router close and focus tabs. Without it everything else
works and closing a tab is manual.

## What is in it

| Piece | Where |
|---|---|
| Router doctrine | `agents/router.md` |
| Child tab and relay skills | `skills/child-role`, `skills/relay`, `skills/install-extension` |
| Order ledger (SQLite, `~/.claude/tab-dispatch/ledger.db`) | `scripts/ledger.py` |
| Done check by exit code | `scripts/done_criterio.py` |
| Tab launch and control | `scripts/tab_launch.py`, `scripts/tab_control.py`, `extension/` |
| Tab title from the role | `hooks/tab_title.py` |

Every figure in this README has its source in [docs/SOURCES.md](docs/SOURCES.md).

License: MIT.
