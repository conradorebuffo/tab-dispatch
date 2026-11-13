---
name: relay
description: Relay or close a child tab on the Router's order. Commits this tab's own work, reports, and closes the session. Use only when an [ORDER r-<id>] says `kind: relay` or `kind: close`, or when the human asks for it.
---

# relay: hand over what you have, report, close

Two kinds of order reach this skill:

- `kind: relay`: your context is full, or the next stretch needs none of it. Leave the state
  so a fresh tab can continue.
- `kind: close`: the work is finished. Leave nothing open and end the session.

`ledger` means `python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py"`.

## Steps

1. **Commit your own work.** Stage only the paths this tab changed and commit them. Do not use a
   whole-index commit: other tabs share the working tree. If you cannot tell which changes are
   yours, do not commit them; list them in the report instead.
2. **Park the thread.** A relay ends with an outcome. Write it with:
   ```
   ledger thread park <thread> --outcome continue|pause|done --context "<what this work is>" --done "<what is finished>" --next "<the next step>"
   ```
   `continue`: a fresh tab goes on now. `pause`: it waits for another day, and the Router's
   attention queue lists it. `done`: nothing is left (`--next` may be empty). The command
   writes `threads/<thread>.md`; the tab that takes it up runs `ledger thread resume <thread>`.
   Use the outcome the order names; if it names none, use `pause`.
3. **Report.**
   ```
   ledger report <id> --state ready --evidence "<commit sha>; thread <thread> <outcome>"
   ```
   If changes are left uncommitted, report `--state blocked` and name them in the evidence.
   Send the printed `[REPORT r-<id>]` block to the Router with `SendMessage`, as printed.
4. **Close the session.** End the conversation. The Router verifies the commit and closes the
   tab by its exact title. Do not close the tab yourself: the Router checks first that the
   session ended.

## Never

- Commit paths that are not yours.
- Close a tab other than your own.
- Report `ready` with uncommitted work.
