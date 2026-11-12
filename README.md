# claude-tab-dispatch

One person, ten Claude Code tabs. A Router tab hands orders to child tabs, and an order
closes only when its `done` command exits 0, not when a tab says it is done.

> Skeleton. The full README (problem, principle, contract with a real example, the sealed
> timeline and its limits, what can break, two-command install) is written in plan step 12.

- Requires Claude Code 2.1.321 or later and the VS Code extension 2.1.322. Tested on Windows only.
- License: MIT.
- Contract between tabs: [docs/CONTRACT.md](docs/CONTRACT.md).
