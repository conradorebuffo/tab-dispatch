'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { matchTabs, validateInput, validateRequest, isClaudePanel } = require('../lib/match');

const NOW = Date.parse('2026-11-11T20:30:00-03:00');
const request = (extra = {}) => ({ action: 'close', title: 'S1-Short', created: new Date(NOW - 5000).toISOString(), ...extra });

test('validateRequest: a current, matching request authorizes', () => {
  assert.deepStrictEqual(validateRequest(request(), { action: 'close', title: 'S1-Short' }, NOW), { ok: true, reason: 'ok' });
});

test('validateRequest: no request', () => {
  assert.strictEqual(validateRequest(null, { action: 'close', title: 'x' }, NOW).reason, 'request_missing');
  assert.strictEqual(validateRequest(undefined, { action: 'close', title: 'x' }, NOW).reason, 'request_missing');
  assert.strictEqual(validateRequest('text', { action: 'close', title: 'x' }, NOW).reason, 'request_missing');
});

test('validateRequest: different action or different title', () => {
  assert.strictEqual(validateRequest(request(), { action: 'focus', title: 'S1-Short' }, NOW).reason, 'request_mismatch');
  assert.strictEqual(validateRequest(request(), { action: 'close', title: 'S2-Other' }, NOW).reason, 'request_mismatch');
});

test('validateRequest: expired at 60 s, valid at 59 s, and clock in the future', () => {
  const uri = { action: 'close', title: 'S1-Short' };
  assert.strictEqual(validateRequest(request({ created: new Date(NOW - 60000).toISOString() }), uri, NOW).reason, 'request_expired');
  assert.strictEqual(validateRequest(request({ created: new Date(NOW - 59000).toISOString() }), uri, NOW).ok, true);
  assert.strictEqual(validateRequest(request({ created: new Date(NOW + 5000).toISOString() }), uri, NOW).reason, 'request_expired');
  assert.strictEqual(validateRequest(request({ created: 'garbage' }), uri, NOW).reason, 'request_expired');
});

test('validateRequest: list does not compare the title', () => {
  assert.strictEqual(validateRequest({ action: 'list', created: new Date(NOW - 1000).toISOString() }, { action: 'list' }, NOW).ok, true);
});

const N = '123e4567-e89b-42d3-a456-426614174000';
const claude = (label) => ({ label, viewType: 'claudeVSCodePanel' });

test('isClaudePanel accepts both forms and rejects others', () => {
  assert.ok(isClaudePanel('claudeVSCodePanel'));
  assert.ok(isClaudePanel('mainThreadWebview-claudeVSCodePanel'));
  assert.ok(!isClaudePanel('claudePlanPreview'));
  assert.ok(!isClaudePanel('mainThreadWebview-claudePlanPreview'));
  assert.ok(!isClaudePanel(undefined));
  assert.ok(!isClaudePanel('markdown.preview'));
});

test('a single exact match acts', () => {
  const r = matchTabs([claude('S1-Short'), claude('S2-Other')], 'S1-Short');
  assert.strictEqual(r.action, 'act');
  assert.strictEqual(r.match, 'exact');
  assert.strictEqual(r.tab.label, 'S1-Short');
});

test('a text file with the same name is not touched', () => {
  const r = matchTabs([{ label: 'S9001-Short', viewType: undefined }], 'S9001-Short');
  assert.strictEqual(r.action, 'not_found');
});

test('a foreign webview with the same label is not touched', () => {
  const r = matchTabs(
    [
      { label: 'S9001-Short', viewType: 'markdown.preview' },
      { label: 'S9001-Short', viewType: 'claudePlanPreview' },
    ],
    'S9001-Short'
  );
  assert.strictEqual(r.action, 'not_found');
});

test('the foreign decoy does not produce a duplicate with the Claude one', () => {
  const r = matchTabs([{ label: 'S9001-Short', viewType: undefined }, claude('S9001-Short')], 'S9001-Short');
  assert.strictEqual(r.action, 'act');
  assert.strictEqual(r.tab.viewType, 'claudeVSCodePanel');
});

test('two identical tabs: duplicate and does not act', () => {
  const r = matchTabs([claude('Claude Code'), claude('Claude Code')], 'Claude Code');
  assert.strictEqual(r.action, 'duplicate');
  assert.strictEqual(r.tab, null);
});

test('long truncated title: acts with a truncated match', () => {
  const title = 'T1234-LongTitleForTheTests'; // 26 chars
  const label = title.slice(0, 24) + '…';
  const r = matchTabs([claude(label), claude('S1-Other')], title);
  assert.strictEqual(r.action, 'act');
  assert.strictEqual(r.match, 'truncated');
});

test('truncation disabled: not_found', () => {
  const title = 'T1234-LongTitleForTheTests';
  const r = matchTabs([claude(title.slice(0, 24) + '…')], title, { allowTruncated: false });
  assert.strictEqual(r.action, 'not_found');
});

test('two truncated tabs with the same prefix: duplicate', () => {
  const title = 'T1234-LongTitleForTheTests';
  const label = title.slice(0, 24) + '…';
  const r = matchTabs([claude(label), claude(label)], title);
  assert.strictEqual(r.action, 'duplicate');
});

test('a short title does not match by truncation', () => {
  const r = matchTabs([claude('S1-Short…')], 'S1-Short');
  assert.strictEqual(r.action, 'not_found');
});

test('nonexistent title', () => {
  const r = matchTabs([claude('S1-A')], 'S9999-DoesNotExist');
  assert.strictEqual(r.action, 'not_found');
});

test('validateInput', () => {
  assert.strictEqual(validateInput({ action: 'close', title: 'x', nonce: N }), null);
  assert.strictEqual(validateInput({ action: 'list', nonce: N }), null);
  assert.strictEqual(validateInput({ action: 'delete', title: 'x', nonce: N }), 'invalid_action');
  assert.strictEqual(validateInput({ action: 'close', title: 'x', nonce: '../../x' }), 'invalid_nonce');
  assert.strictEqual(validateInput({ action: 'close', title: 'x' }), 'invalid_nonce');
  assert.strictEqual(validateInput({ action: 'close', title: '', nonce: N }), 'invalid_title');
  assert.strictEqual(validateInput({ action: 'focus', title: 'x'.repeat(201), nonce: N }), 'invalid_title');
  assert.strictEqual(validateInput({ action: 'close', nonce: N }), 'invalid_title');
});
