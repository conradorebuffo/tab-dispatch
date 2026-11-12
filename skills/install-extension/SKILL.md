---
name: install-extension
description: Install the VS Code extension that ships inside this plugin, so the Router can close and focus tabs by exact title. Use once after installing the plugin, or when the Router reports that tab control does not answer.
---

# install-extension: put the tab-control extension into VS Code

The plugin carries the extension as a `.vsix` file. The Router needs it to close and focus tabs.
Without it, everything else works, and closing a tab is a manual step.

## Steps

1. **Check first.** Run:
   ```
   code --list-extensions --show-versions
   ```
   If `conradorebuffo.tab-dispatch` is listed at the plugin version, stop: it is installed.
2. **Install.** Run:
   ```
   code --install-extension "${CLAUDE_PLUGIN_ROOT}/extension/dist/tab-dispatch-0.1.0.vsix"
   ```
   Measured on 2026-11-12: this installs without a dialog and prints
   `Extension '...vsix' was successfully installed.` Windows only.
3. **Reload VS Code** (command palette: `Developer: Reload Window`) so the extension activates.
4. **Verify.** Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/tab_control.py" list`. It lists the Claude
   Code tabs. The first call after install can show a VS Code dialog that asks the human to allow
   the extension to open a URI: that click belongs to the human, not to you.

## If it does not work

Say what happened, with the command and its output, and stop. Do not install other extensions
and do not edit the VS Code settings.
