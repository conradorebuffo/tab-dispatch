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

## When to use this

It fits when all of this is true:

- **You work inside the Claude Code extension for VS Code.** The tabs are session tabs of that
  extension. Opening, naming and closing them goes through the extension; a session in a
  terminal can keep the ledger, and nothing else.
- **You are on native Windows.** It was written and tested on Windows 11, without WSL, and the
  CI runs on `windows-latest`. Nothing else was tested.
- **A command can check the work.** An order is done when its `done` command exits 0. If no
  command can tell that a piece of work is finished, the ledger has nothing to verify.
- **You do not need a lock.** `file_overlap.py` warns when two orders name the same file. It is
  a warning, not a protection: nothing stops two tabs from editing one file.

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

## A working session

What 0.2 adds are the flows of a real session, each one small and kept in files and in the
ledger:

- **Names.** A tab is named `S<n>-<Title>`: `S1-Router`, `S2-PythonTests`. `<n>` is counted in
  the ledger the first time a tab declares its role, and the name stays for the whole session.
- **Model and effort per order.** `ledger assign --model sonnet --effort medium` writes
  `model:` and `effort:` in the order. It is a declared requirement: no message switches the
  model of another tab, so a child on another model reports `waiting`.
- **Attention queue.** `ledger attention` shows what waits for the human, one item at a time:
  questions, blocked and failed orders, plans to approve, paused threads. The `attention`
  skill dispatches each answer.
- **Plan orders.** `--kind plan`: the child writes `plans/NNN_slug.md` with one done command
  per step and waits. After the human approves, it executes, and `ledger plan check` runs
  every step's command.
- **Threads.** A relay ends with `continue`, `pause` or `done` and leaves `threads/<id>.md`
  (context, done, next step). A new session takes it up with `ledger thread resume`.
- **Session report.** `ledger session report --out <file>` writes the orders, the exit codes
  of the done checks, the times and the lessons recorded with `ledger lesson add`.
- **Prompt count.** `scripts/prompt_count.py` counts, per tab, the messages the human typed and
  the ones other tabs sent. Counts only: it never stores or prints the text of a message.
  They are raw counts of one run; nothing is compared.

## The timeline, and its limit

The design notes of this approach were timestamped on 2026-11-11 under RFC 3161 by two
independent authorities, DigiCert and Sectigo. That proves the notes existed by those times.
It does not prove that the approach works, and it says nothing about results. What was
measured is described when it is published.

## What can break

- It depends on behavior that is not documented: the `viewType` of the Claude Code webview
  and the way VS Code cuts a tab title to 24 characters. Both fail on the safe side: a tab
  that cannot be matched by exact title is not closed.
- The label of an open tab changes when the tab receives a message: the title hook hands the
  name to Claude Code at session start and at each message (`sessionTitle` in the hook output,
  measured with Claude Code 2.1.321). The extension does not watch the title file on disk
  (measured with the extension 2.1.322). So a tab named after its last message still reads
  "Claude Code", and it cannot be closed by title. The Router names a tab it opens before the
  first order. The hooks run only when the plugin is installed; a copy of the folder that is
  not installed writes the title to disk and nothing more.
- `model:` and `effort:` in an order are not enforced. A person sets them by typing `/model`
  and `/effort` in the tab.
- The prompt count cannot see an approved permission dialog: approving leaves no line in the
  transcript.
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
| Child tab, relay and attention skills | `skills/child-role`, `skills/relay`, `skills/attention`, `skills/install-extension` |
| Order ledger (SQLite, `~/.claude/tab-dispatch/ledger.db`): orders, plans, threads, attention, session report | `scripts/ledger.py` |
| Done check by exit code | `scripts/done_criterio.py` |
| Tab launch and control | `scripts/tab_launch.py`, `scripts/tab_control.py`, `extension/` |
| Tab title `S<n>-<Title>` from the role of the tab, at session start and at each message | `hooks/tab_title.py`, `hooks/hooks.json`, `scripts/titles.py` |
| Prompt count per tab | `scripts/prompt_count.py` |

Every figure in this README has its source in [docs/SOURCES.md](docs/SOURCES.md).

License: MIT.
