'use strict';
// Pure tab-matching logic. No dependency on vscode.

const ACTIONS = ['list', 'close', 'focus'];
const VIEW_TYPE = 'claudeVSCodePanel';
const NONCE_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_TITLE = 200;
const TRUNCATION_LENGTH = 25; // 24 characters + "…"

function isClaudePanel(viewType) {
  if (typeof viewType !== 'string') return false;
  return viewType === VIEW_TYPE || viewType.endsWith('-' + VIEW_TYPE);
}

function validateInput({ action, title, nonce }) {
  if (!ACTIONS.includes(action)) return 'invalid_action';
  if (typeof nonce !== 'string' || !NONCE_RE.test(nonce)) return 'invalid_nonce';
  if (action !== 'list') {
    if (typeof title !== 'string' || title.length < 1 || title.length > MAX_TITLE) {
      return 'invalid_title';
    }
  }
  return null;
}

/**
 * @param {Array<{label:string, viewType:string}>} tabs
 * @param {string} title
 * @param {{allowTruncated?: boolean}} [options]
 * @returns {{action:'act'|'not_found'|'duplicate', tab:object|null, reason:string, match:'exact'|'truncated'|null}}
 */
function matchTabs(tabs, title, options = {}) {
  const allowTruncated = options.allowTruncated !== false;
  const claude = (tabs || []).filter((t) => t && isClaudePanel(t.viewType));
  const exact = claude.filter((t) => t.label === title);
  if (exact.length === 1) return { action: 'act', tab: exact[0], reason: 'ok', match: 'exact' };
  if (exact.length > 1) {
    return { action: 'duplicate', tab: null, reason: exact.length + ' tabs with the same title', match: null };
  }
  if (allowTruncated && title.length > TRUNCATION_LENGTH - 1) {
    const prefix = title.slice(0, TRUNCATION_LENGTH - 1);
    const truncated = claude.filter(
      (t) =>
        typeof t.label === 'string' &&
        t.label.length === TRUNCATION_LENGTH &&
        t.label.endsWith('…') &&
        t.label.slice(0, TRUNCATION_LENGTH - 1) === prefix
    );
    if (truncated.length === 1) return { action: 'act', tab: truncated[0], reason: 'ok', match: 'truncated' };
    if (truncated.length > 1) {
      return {
        action: 'duplicate',
        tab: null,
        reason: truncated.length + ' truncated tabs with the same prefix',
        match: null,
      };
    }
  }
  return { action: 'not_found', tab: null, reason: 'no Claude Code tab with that title', match: null };
}

const REQUEST_TTL_MS = 60 * 1000;

/**
 * Decides whether a pre-registered request authorizes the action of a URI.
 * The nonce only identifies; what authorizes is that the Router wrote the request to disk beforehand.
 * @param {object|null|undefined} request content of requests/<nonce>.json, or null if it does not exist
 * @param {{action:string, title?:string}} uri what the URI carries
 * @param {number} nowMs current clock in ms
 * @returns {{ok:boolean, reason:string}} reason: ok | request_missing | request_mismatch | request_expired
 */
function validateRequest(request, uri, nowMs) {
  if (!request || typeof request !== 'object') return { ok: false, reason: 'request_missing' };
  const created = Date.parse(request.created);
  if (!Number.isFinite(created)) return { ok: false, reason: 'request_expired' };
  const age = nowMs - created;
  if (age < 0 || age >= REQUEST_TTL_MS) return { ok: false, reason: 'request_expired' };
  const uriTitle = uri.action === 'list' ? undefined : uri.title;
  const requestTitle = uri.action === 'list' ? undefined : request.title;
  if (request.action !== uri.action || requestTitle !== uriTitle) return { ok: false, reason: 'request_mismatch' };
  return { ok: true, reason: 'ok' };
}

module.exports = { matchTabs, validateInput, validateRequest, isClaudePanel, ACTIONS, VIEW_TYPE, MAX_TITLE, TRUNCATION_LENGTH, REQUEST_TTL_MS };
