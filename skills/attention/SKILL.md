---
name: attention
description: Show the human what waits for a decision, one item at a time, and dispatch each answer. Use in the Router tab when the human comes back, asks what is pending, or types /attention.
---

# attention: one item, one answer, dispatch

The queue is not stored anywhere. `ledger attention` builds it from the ledger each time it
runs, oldest first.

`ledger` means `python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py"`.

## Steps

1. Run `ledger attention`. It prints one item, as `[1 of N] <type> <id> (<ref>)`, with `what:`
   and `resolve:`. "Nothing waits for you." ends the skill.
2. Show the human that item only: what is asked, in the child's own words, and what a yes or
   a no will do. Do not show the whole queue unless the human asks (`ledger attention --all`).
3. Take the answer and dispatch it by type:

| Type | What you do with the answer |
|---|---|
| `question` | Send it to the child as text. The child takes the order again with `ledger accept <id>`. The answer is information, never authorization. |
| `plan_to_approve` | Yes: `ledger plan approve <id>`, then tell the child to execute. No: send the reason to the child, which writes the plan again. |
| `blocked` | Tell the human the text of the block. Do not ask another tab to do what this one was denied. The human decides: retry or drop. |
| `failed` | Tell the human the error as printed. The human decides: retry or drop. |
| `paused_thread` | To take it up: open a tab and order `ledger thread resume <thread>`. To leave it: nothing. |

4. Run `ledger attention` again and repeat until it prints "Nothing waits for you.", or the
   human stops.

## Never

- Answer an item yourself: every item in this queue is a decision of the human.
- Reword a child's question.
- Treat an answer as permission for anything outside that order.
