'use strict';
// Mini extension conradorebuffo.tab-dispatch.
// Handles vscode://conradorebuffo.tab-dispatch/{list|close|focus}?title=..&nonce=<uuid>
// Only touches Claude Code tabs (webview claudeVSCodePanel), by exact title.

const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { matchTabs, validateInput, validateRequest } = require('./lib/match');

function baseDir() {
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(local, 'TabDispatch');
}

function currentTabs() {
  const out = [];
  const groups = vscode.window.tabGroups.all;
  groups.forEach((group, g) => {
    group.tabs.forEach((tab, i) => {
      const input = tab.input;
      const isWebview = input instanceof vscode.TabInputWebview;
      out.push({
        label: tab.label,
        viewType: isWebview ? input.viewType : undefined,
        kind: isWebview ? 'webview' : input && input.constructor ? input.constructor.name : 'other',
        group: g,
        index: i,
        active: tab.isActive,
        _tab: tab,
      });
    });
  });
  return out;
}

function view(t) {
  return { label: t.label, viewType: t.viewType, kind: t.kind, group: t.group, index: t.index, active: t.active };
}

function writeResult(nonce, result) {
  // The path is fixed: the URI never chooses where this writes (the nonce was already validated as a UUID).
  const dir = path.join(baseDir(), 'results');
  fs.mkdirSync(dir, { recursive: true });
  const body = JSON.stringify(result);
  const final = path.join(dir, nonce + '.json');
  const tmp = final + '.tmp';
  fs.writeFileSync(tmp, body, 'utf8');
  fs.renameSync(tmp, final);
  fs.appendFileSync(path.join(baseDir(), 'log.jsonl'), body + '\n', 'utf8');
}

function logEvent(obj) {
  try {
    fs.mkdirSync(baseDir(), { recursive: true });
    fs.appendFileSync(path.join(baseDir(), 'log.jsonl'), JSON.stringify(obj) + '\n', 'utf8');
  } catch (e) {
    console.error('[conradorebuffo.tab-dispatch] could not write the log:', e);
  }
}

// Reads requests/<nonce>.json, deletes it (valid only once, even if it does not match) and validates it.
function consumeRequest(nonce, uri) {
  const file = path.join(baseDir(), 'requests', nonce + '.json');
  let request = null;
  try {
    request = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    if (e && e.code !== 'ENOENT') console.error('[conradorebuffo.tab-dispatch] unreadable request:', e);
    request = null;
  }
  if (request !== null) {
    try {
      fs.unlinkSync(file);
    } catch (e) {
      console.error('[conradorebuffo.tab-dispatch] could not delete the request, not acting:', e);
      return { ok: false, reason: 'request_not_consumable' };
    }
  }
  return validateRequest(request, uri, Date.now());
}

async function focusTab(tab) {
  const group = tab.group;
  const n = group + 1;
  if (n < 1 || n > 8) return { ok: false, reason: 'unsupported: group outside 1..8' };
  await vscode.commands.executeCommand('workbench.action.focus' + ['First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth', 'Seventh', 'Eighth'][group] + 'EditorGroup');
  await vscode.commands.executeCommand('workbench.action.openEditorAtIndex', tab.index);
  const after = currentTabs().find((t) => t.group === tab.group && t.index === tab.index);
  return after && after.active ? { ok: true, reason: 'ok' } : { ok: false, reason: 'unsupported: the tab did not become active' };
}

async function handle(uri) {
  const params = new URLSearchParams(uri.query);
  const action = uri.path.replace(/^\/+/, '');
  const title = params.get('title') || undefined;
  const nonce = params.get('nonce') || undefined;
  const base = {
    ts: new Date().toISOString(),
    action,
    window: vscode.workspace.name || null,
    vscode_session: vscode.env.sessionId,
  };
  const error = validateInput({ action, title, nonce });
  if (error) {
    // Without a valid nonce no result is written: there is nowhere to put it.
    try {
      fs.mkdirSync(baseDir(), { recursive: true });
      fs.appendFileSync(path.join(baseDir(), 'log.jsonl'), JSON.stringify({ ...base, ok: false, reason: error }) + '\n', 'utf8');
    } catch (e) {
      console.error('[conradorebuffo.tab-dispatch] could not write the log:', e);
    }
    return;
  }
  // The nonce only identifies: the authorization is the request the Router left written before launching the URI.
  const requestDecision = consumeRequest(nonce, { action, title });
  if (!requestDecision.ok) {
    logEvent({ ...base, ok: false, reason: requestDecision.reason, nonce });
    return; // without a valid request nothing is done and no result is written
  }
  let result;
  try {
    const tabs = currentTabs();
    const labels = tabs.map(view);
    if (action === 'list') {
      result = { ...base, ok: true, reason: 'ok', match: null, tabs: labels };
    } else {
      const decision = matchTabs(tabs, title);
      if (decision.action !== 'act') {
        result = { ...base, ok: false, reason: decision.action, detail: decision.reason, match: null, title, tabs: labels };
      } else if (action === 'close') {
        const closed = await vscode.window.tabGroups.close(decision.tab._tab, true);
        result = { ...base, ok: !!closed, reason: closed ? 'ok' : 'not_closed', match: decision.match, title, closed: view(decision.tab), tabs: labels };
      } else {
        const r = await focusTab(decision.tab);
        result = { ...base, ok: r.ok, reason: r.reason, match: decision.match, title, focused: view(decision.tab), tabs: labels };
      }
    }
  } catch (e) {
    result = { ...base, ok: false, reason: 'error', detail: String(e && e.message ? e.message : e) };
  }
  try {
    writeResult(nonce, result);
  } catch (e) {
    console.error('[conradorebuffo.tab-dispatch] could not write the result:', e);
  }
}

function activate(context) {
  context.subscriptions.push(vscode.window.registerUriHandler({ handleUri: handle }));
}

function deactivate() {}

module.exports = { activate, deactivate };
