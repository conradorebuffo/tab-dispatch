'use strict';
// Lógica pura de cotejo de pestañas. Sin dependencias de vscode.

const ACCIONES = ['list', 'close', 'focus'];
const VIEW_TYPE = 'claudeVSCodePanel';
const NONCE_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_TITULO = 200;
const RECORTE_LARGO = 25; // 24 caracteres + "…"

function esClaudePanel(viewType) {
  if (typeof viewType !== 'string') return false;
  return viewType === VIEW_TYPE || viewType.endsWith('-' + VIEW_TYPE);
}

function validarEntrada({ accion, title, nonce }) {
  if (!ACCIONES.includes(accion)) return 'accion_invalida';
  if (typeof nonce !== 'string' || !NONCE_RE.test(nonce)) return 'nonce_invalido';
  if (accion !== 'list') {
    if (typeof title !== 'string' || title.length < 1 || title.length > MAX_TITULO) {
      return 'titulo_invalido';
    }
  }
  return null;
}

/**
 * @param {Array<{label:string, viewType:string}>} tabs
 * @param {string} titulo
 * @param {{permitirTruncado?: boolean}} [opciones]
 * @returns {{accion:'actuar'|'no_existe'|'duplicada', tab:object|null, motivo:string, match:'exacto'|'truncado'|null}}
 */
function cotejar(tabs, titulo, opciones = {}) {
  const permitirTruncado = opciones.permitirTruncado !== false;
  const claude = (tabs || []).filter((t) => t && esClaudePanel(t.viewType));
  const exactas = claude.filter((t) => t.label === titulo);
  if (exactas.length === 1) return { accion: 'actuar', tab: exactas[0], motivo: 'ok', match: 'exacto' };
  if (exactas.length > 1) {
    return { accion: 'duplicada', tab: null, motivo: exactas.length + ' pestañas con el mismo título', match: null };
  }
  if (permitirTruncado && titulo.length > RECORTE_LARGO - 1) {
    const prefijo = titulo.slice(0, RECORTE_LARGO - 1);
    const recortadas = claude.filter(
      (t) =>
        typeof t.label === 'string' &&
        t.label.length === RECORTE_LARGO &&
        t.label.endsWith('…') &&
        t.label.slice(0, RECORTE_LARGO - 1) === prefijo
    );
    if (recortadas.length === 1) return { accion: 'actuar', tab: recortadas[0], motivo: 'ok', match: 'truncado' };
    if (recortadas.length > 1) {
      return {
        accion: 'duplicada',
        tab: null,
        motivo: recortadas.length + ' pestañas recortadas con el mismo prefijo',
        match: null,
      };
    }
  }
  return { accion: 'no_existe', tab: null, motivo: 'ninguna pestaña de Claude Code con ese título', match: null };
}

const PEDIDO_VIGENCIA_MS = 60 * 1000;

/**
 * Decide si un pedido pre-registrado autoriza la acción de una URI.
 * El nonce sólo identifica; lo que autoriza es que el Router haya escrito antes el pedido en disco.
 * @param {object|null|undefined} pedido contenido de pedidos/<nonce>.json, o null si no existe
 * @param {{accion:string, title?:string}} uri lo que trae la URI
 * @param {number} ahoraMs reloj actual en ms
 * @returns {{ok:boolean, motivo:string}} motivo: ok | pedido_inexistente | pedido_distinto | pedido_vencido
 */
function validarPedido(pedido, uri, ahoraMs) {
  if (!pedido || typeof pedido !== 'object') return { ok: false, motivo: 'pedido_inexistente' };
  const creado = Date.parse(pedido.creado);
  if (!Number.isFinite(creado)) return { ok: false, motivo: 'pedido_vencido' };
  const edad = ahoraMs - creado;
  if (edad < 0 || edad >= PEDIDO_VIGENCIA_MS) return { ok: false, motivo: 'pedido_vencido' };
  const tituloUri = uri.accion === 'list' ? undefined : uri.title;
  const tituloPedido = uri.accion === 'list' ? undefined : pedido.title;
  if (pedido.accion !== uri.accion || tituloPedido !== tituloUri) return { ok: false, motivo: 'pedido_distinto' };
  return { ok: true, motivo: 'ok' };
}

module.exports = { cotejar, validarEntrada, validarPedido, esClaudePanel, ACCIONES, VIEW_TYPE, MAX_TITULO, RECORTE_LARGO, PEDIDO_VIGENCIA_MS };
