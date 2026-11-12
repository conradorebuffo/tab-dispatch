'use strict';
// Mini extension conradorebuffo.tab-dispatch.
// Maneja vscode://conradorebuffo.tab-dispatch/{list|close|focus}?title=..&nonce=<uuid>
// Sólo toca pestañas de Claude Code (webview claudeVSCodePanel), por título exacto.

const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { cotejar, validarEntrada, validarPedido } = require('./lib/cotejo');

function baseDir() {
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(local, 'TabDispatch');
}

function tabsActuales() {
  const out = [];
  const grupos = vscode.window.tabGroups.all;
  grupos.forEach((grupo, g) => {
    grupo.tabs.forEach((tab, i) => {
      const input = tab.input;
      const esWebview = input instanceof vscode.TabInputWebview;
      out.push({
        label: tab.label,
        viewType: esWebview ? input.viewType : undefined,
        tipo: esWebview ? 'webview' : input && input.constructor ? input.constructor.name : 'otro',
        grupo: g,
        indice: i,
        activa: tab.isActive,
        _tab: tab,
      });
    });
  });
  return out;
}

function vista(t) {
  return { label: t.label, viewType: t.viewType, tipo: t.tipo, grupo: t.grupo, indice: t.indice, activa: t.activa };
}

function escribirResultado(nonce, resultado) {
  // La ruta es fija: la URI nunca elige dónde se escribe (el nonce ya fue validado como UUID).
  const dir = path.join(baseDir(), 'resultados');
  fs.mkdirSync(dir, { recursive: true });
  const cuerpo = JSON.stringify(resultado);
  const final = path.join(dir, nonce + '.json');
  const tmp = final + '.tmp';
  fs.writeFileSync(tmp, cuerpo, 'utf8');
  fs.renameSync(tmp, final);
  fs.appendFileSync(path.join(baseDir(), 'log.jsonl'), cuerpo + '\n', 'utf8');
}

function registrarLog(obj) {
  try {
    fs.mkdirSync(baseDir(), { recursive: true });
    fs.appendFileSync(path.join(baseDir(), 'log.jsonl'), JSON.stringify(obj) + '\n', 'utf8');
  } catch (e) {
    console.error('[conradorebuffo.tab-dispatch] no pude escribir el log:', e);
  }
}

// Lee pedidos/<nonce>.json, lo borra (vale una sola vez, aunque no coincida) y lo valida.
function consumirPedido(nonce, uri) {
  const archivo = path.join(baseDir(), 'pedidos', nonce + '.json');
  let pedido = null;
  try {
    pedido = JSON.parse(fs.readFileSync(archivo, 'utf8'));
  } catch (e) {
    if (e && e.code !== 'ENOENT') console.error('[conradorebuffo.tab-dispatch] pedido ilegible:', e);
    pedido = null;
  }
  if (pedido !== null) {
    try {
      fs.unlinkSync(archivo);
    } catch (e) {
      console.error('[conradorebuffo.tab-dispatch] no pude borrar el pedido, no se actúa:', e);
      return { ok: false, motivo: 'pedido_no_consumible' };
    }
  }
  return validarPedido(pedido, uri, Date.now());
}

async function enfocar(tab) {
  const grupo = tab.grupo;
  const n = grupo + 1;
  if (n < 1 || n > 8) return { ok: false, motivo: 'no_soportado: grupo fuera de 1..8' };
  await vscode.commands.executeCommand('workbench.action.focus' + ['First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth', 'Seventh', 'Eighth'][grupo] + 'EditorGroup');
  await vscode.commands.executeCommand('workbench.action.openEditorAtIndex', tab.indice);
  const despues = tabsActuales().find((t) => t.grupo === tab.grupo && t.indice === tab.indice);
  return despues && despues.activa ? { ok: true, motivo: 'ok' } : { ok: false, motivo: 'no_soportado: la pestaña no quedó activa' };
}

async function manejar(uri) {
  const params = new URLSearchParams(uri.query);
  const accion = uri.path.replace(/^\/+/, '');
  const title = params.get('title') || undefined;
  const nonce = params.get('nonce') || undefined;
  const base = {
    ts: new Date().toISOString(),
    accion,
    ventana: vscode.workspace.name || null,
    sesion_vscode: vscode.env.sessionId,
  };
  const error = validarEntrada({ accion, title, nonce });
  if (error) {
    // Sin nonce válido no se escribe resultado: no hay a dónde.
    try {
      fs.mkdirSync(baseDir(), { recursive: true });
      fs.appendFileSync(path.join(baseDir(), 'log.jsonl'), JSON.stringify({ ...base, ok: false, motivo: error }) + '\n', 'utf8');
    } catch (e) {
      console.error('[conradorebuffo.tab-dispatch] no pude escribir el log:', e);
    }
    return;
  }
  // El nonce sólo identifica: la autorización es el pedido que el Router dejó escrito antes de lanzar la URI.
  const decisionPedido = consumirPedido(nonce, { accion, title });
  if (!decisionPedido.ok) {
    registrarLog({ ...base, ok: false, motivo: decisionPedido.motivo, nonce });
    return; // sin pedido válido no se actúa ni se escribe resultado
  }
  let resultado;
  try {
    const tabs = tabsActuales();
    const etiquetas = tabs.map(vista);
    if (accion === 'list') {
      resultado = { ...base, ok: true, motivo: 'ok', match: null, tabs: etiquetas };
    } else {
      const decision = cotejar(tabs, title);
      if (decision.accion !== 'actuar') {
        resultado = { ...base, ok: false, motivo: decision.accion, detalle: decision.motivo, match: null, title, tabs: etiquetas };
      } else if (accion === 'close') {
        const cerrada = await vscode.window.tabGroups.close(decision.tab._tab, true);
        resultado = { ...base, ok: !!cerrada, motivo: cerrada ? 'ok' : 'no_cerro', match: decision.match, title, cerrada: vista(decision.tab), tabs: etiquetas };
      } else {
        const r = await enfocar(decision.tab);
        resultado = { ...base, ok: r.ok, motivo: r.motivo, match: decision.match, title, enfocada: vista(decision.tab), tabs: etiquetas };
      }
    }
  } catch (e) {
    resultado = { ...base, ok: false, motivo: 'error', detalle: String(e && e.message ? e.message : e) };
  }
  try {
    escribirResultado(nonce, resultado);
  } catch (e) {
    console.error('[conradorebuffo.tab-dispatch] no pude escribir el resultado:', e);
  }
}

function activate(context) {
  context.subscriptions.push(vscode.window.registerUriHandler({ handleUri: manejar }));
}

function deactivate() {}

module.exports = { activate, deactivate };
