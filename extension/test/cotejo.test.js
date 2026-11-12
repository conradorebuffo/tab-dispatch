'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { cotejar, validarEntrada, validarPedido, esClaudePanel } = require('../lib/cotejo');

const AHORA = Date.parse('2026-11-11T20:30:00-03:00');
const pedido = (extra = {}) => ({ accion: 'close', title: 'S1-Corto', creado: new Date(AHORA - 5000).toISOString(), ...extra });

test('validarPedido: pedido vigente y coincidente autoriza', () => {
  assert.deepStrictEqual(validarPedido(pedido(), { accion: 'close', title: 'S1-Corto' }, AHORA), { ok: true, motivo: 'ok' });
});

test('validarPedido: sin pedido', () => {
  assert.strictEqual(validarPedido(null, { accion: 'close', title: 'x' }, AHORA).motivo, 'pedido_inexistente');
  assert.strictEqual(validarPedido(undefined, { accion: 'close', title: 'x' }, AHORA).motivo, 'pedido_inexistente');
  assert.strictEqual(validarPedido('texto', { accion: 'close', title: 'x' }, AHORA).motivo, 'pedido_inexistente');
});

test('validarPedido: otra acción o otro título', () => {
  assert.strictEqual(validarPedido(pedido(), { accion: 'focus', title: 'S1-Corto' }, AHORA).motivo, 'pedido_distinto');
  assert.strictEqual(validarPedido(pedido(), { accion: 'close', title: 'S2-Otro' }, AHORA).motivo, 'pedido_distinto');
});

test('validarPedido: vencido a los 60 s, vigente a los 59 s, y reloj en el futuro', () => {
  const uri = { accion: 'close', title: 'S1-Corto' };
  assert.strictEqual(validarPedido(pedido({ creado: new Date(AHORA - 60000).toISOString() }), uri, AHORA).motivo, 'pedido_vencido');
  assert.strictEqual(validarPedido(pedido({ creado: new Date(AHORA - 59000).toISOString() }), uri, AHORA).ok, true);
  assert.strictEqual(validarPedido(pedido({ creado: new Date(AHORA + 5000).toISOString() }), uri, AHORA).motivo, 'pedido_vencido');
  assert.strictEqual(validarPedido(pedido({ creado: 'basura' }), uri, AHORA).motivo, 'pedido_vencido');
});

test('validarPedido: list no compara título', () => {
  assert.strictEqual(validarPedido({ accion: 'list', creado: new Date(AHORA - 1000).toISOString() }, { accion: 'list' }, AHORA).ok, true);
});

const N = '123e4567-e89b-42d3-a456-426614174000';
const claude = (label) => ({ label, viewType: 'claudeVSCodePanel' });

test('esClaudePanel acepta las dos formas y rechaza otros', () => {
  assert.ok(esClaudePanel('claudeVSCodePanel'));
  assert.ok(esClaudePanel('mainThreadWebview-claudeVSCodePanel'));
  assert.ok(!esClaudePanel('claudePlanPreview'));
  assert.ok(!esClaudePanel('mainThreadWebview-claudePlanPreview'));
  assert.ok(!esClaudePanel(undefined));
  assert.ok(!esClaudePanel('markdown.preview'));
});

test('coincidencia exacta única actúa', () => {
  const r = cotejar([claude('S1-Corto'), claude('S2-Otro')], 'S1-Corto');
  assert.strictEqual(r.accion, 'actuar');
  assert.strictEqual(r.match, 'exacto');
  assert.strictEqual(r.tab.label, 'S1-Corto');
});

test('un archivo de texto con el mismo nombre no se toca', () => {
  const r = cotejar([{ label: 'S9001-Corto', viewType: undefined }], 'S9001-Corto');
  assert.strictEqual(r.accion, 'no_existe');
});

test('un webview ajeno con el mismo label no se toca', () => {
  const r = cotejar(
    [
      { label: 'S9001-Corto', viewType: 'markdown.preview' },
      { label: 'S9001-Corto', viewType: 'claudePlanPreview' },
    ],
    'S9001-Corto'
  );
  assert.strictEqual(r.accion, 'no_existe');
});

test('el señuelo ajeno no genera duplicada con la de Claude', () => {
  const r = cotejar([{ label: 'S9001-Corto', viewType: undefined }, claude('S9001-Corto')], 'S9001-Corto');
  assert.strictEqual(r.accion, 'actuar');
  assert.strictEqual(r.tab.viewType, 'claudeVSCodePanel');
});

test('dos pestañas iguales: duplicada y no actúa', () => {
  const r = cotejar([claude('Claude Code'), claude('Claude Code')], 'Claude Code');
  assert.strictEqual(r.accion, 'duplicada');
  assert.strictEqual(r.tab, null);
});

test('título largo recortado: actúa con match truncado', () => {
  const titulo = 'T1234-LongTitleForTheTests'; // 26 chars
  const label = titulo.slice(0, 24) + '…';
  const r = cotejar([claude(label), claude('S1-Otro')], titulo);
  assert.strictEqual(r.accion, 'actuar');
  assert.strictEqual(r.match, 'truncado');
});

test('recorte desactivado: no_existe', () => {
  const titulo = 'T1234-LongTitleForTheTests';
  const r = cotejar([claude(titulo.slice(0, 24) + '…')], titulo, { permitirTruncado: false });
  assert.strictEqual(r.accion, 'no_existe');
});

test('dos recortadas con el mismo prefijo: duplicada', () => {
  const titulo = 'T1234-LongTitleForTheTests';
  const label = titulo.slice(0, 24) + '…';
  const r = cotejar([claude(label), claude(label)], titulo);
  assert.strictEqual(r.accion, 'duplicada');
});

test('un título corto no matchea por recorte', () => {
  const r = cotejar([claude('S1-Corto…')], 'S1-Corto');
  assert.strictEqual(r.accion, 'no_existe');
});

test('título inexistente', () => {
  const r = cotejar([claude('S1-A')], 'S9999-NoExiste');
  assert.strictEqual(r.accion, 'no_existe');
});

test('validarEntrada', () => {
  assert.strictEqual(validarEntrada({ accion: 'close', title: 'x', nonce: N }), null);
  assert.strictEqual(validarEntrada({ accion: 'list', nonce: N }), null);
  assert.strictEqual(validarEntrada({ accion: 'borrar', title: 'x', nonce: N }), 'accion_invalida');
  assert.strictEqual(validarEntrada({ accion: 'close', title: 'x', nonce: '../../x' }), 'nonce_invalido');
  assert.strictEqual(validarEntrada({ accion: 'close', title: 'x' }), 'nonce_invalido');
  assert.strictEqual(validarEntrada({ accion: 'close', title: '', nonce: N }), 'titulo_invalido');
  assert.strictEqual(validarEntrada({ accion: 'focus', title: 'x'.repeat(201), nonce: N }), 'titulo_invalido');
  assert.strictEqual(validarEntrada({ accion: 'close', nonce: N }), 'titulo_invalido');
});
