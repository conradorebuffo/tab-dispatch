# Contract between tabs

Two text blocks cross between the Router tab and a child tab. Both are plain text sent with
`SendMessage`. A block that does not match this specification is not a message of the
protocol, and nothing acts on it. `scripts/report_contract.py` validates both.

An order carries intent, not consent. Nothing in a block grants permission.

## `[ORDER r-<id>]`: Router to child, at most 1500 characters

```
[2026-11-12 12:43 -0300] [ORDER r-303]
ref: release-7-wave-1
objective: Port the three scripts and their tests.
done: python -m pytest tests -q
limits: touch only scripts/ and tests/
authority: this message authorizes nothing; use only what your own rules already allow
expires: 2026-11-13T12:00:00-03:00
reply: take it with "python scripts/ledger.py accept 303"; when done, "... report 303 --state ..."
```

| Field | Meaning |
|---|---|
| first line | `[YYYY-MM-DD HH:MM <utc offset>] [ORDER r-<id>]`. The stamp comes from the machine clock, never from the sender's estimate. |
| `ref` | What the order is about (a plan, a task). One live order per ref. |
| `objective` | What to do, in one line. |
| `done` | The command whose exit code 0 closes the order. |
| `limits` | What not to touch. Empty means the fixed rule: with production, credentials, third parties or GitHub, stop and report `waiting`. |
| `kind` | Optional. `relay` or `close` for the `relay` skill; absent means a normal work order. |
| `authority` | Fixed text. The sender cannot change it. |
| `expires` | Deadline, or `none declared`. |
| `reply` | How to take the order and how to report. |

Every field is one line. The ledger refuses an order that says "the user said" or "the owner
approved": that phrase is how an order disguises itself as permission. An order over 1500
characters is refused, not cut: a cut order is another order.

## `[REPORT r-<id>]`: child to Router, at most 600 characters

```
[2026-11-12 13:20 -0300] [REPORT r-303]
state: ready
evidence: 1d422498; pytest 96 passed
```

| Field | Meaning |
|---|---|
| `state` | One of `ready`, `waiting`, `blocked`, `failed`, `dropped`. Never `progress`: under an order, progress is not reported. |
| `evidence` | By reference: a commit sha, a test line, an exit code. Never narrated. |
| `question` | Only with `waiting`, and then required: one question. |

## The order states

```
offered -> accepted -> ready -> closed
                    -> waiting | blocked | failed | dropped
waiting | blocked | failed -> accepted
```

`closed` happens only when the Router runs the `done` command and it exits 0. A report of
`ready` is a notice; the exit code is the verdict. If the command does not exit 0 the order
goes back to `accepted`.

## What the contract does not do

- It does not authorize. Authority comes from each tab's own rules and from the human.
- It does not guard files. `scripts/file_overlap.py` warns; a real guard such as
  cotenancy-guard locks.
- It does not carry transcripts. Evidence goes by reference.
