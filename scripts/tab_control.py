#!/usr/bin/env python
"""tab_control: cerrar o enfocar una pestaña de Claude Code vía la extensión conradorebuffo.claude-tab-dispatch.

Es el MECANISMO: no decide si una pestaña se puede cerrar (eso es la política del Router). Pasos de cada acción:

1. Comprueba que la instancia de VS Code destino esté corriendo. Si no, no lanza nada: con
   ``--open-url`` Code.exe abriría un VS Code nuevo.
2. Escribe el PEDIDO ``%LOCALAPPDATA%\\ClaudeTabDispatch\\pedidos\\<nonce>.json`` (accion, title, creado).
   La extensión sólo actúa si ese archivo existe, coincide y tiene menos de 60 s: el nonce de la
   URI solo no autoriza nada .
3. Lanza ``Code.exe [--user-data-dir X] --open-url -- vscode://conradorebuffo.claude-tab-dispatch/<accion>?...``
   reusando ``resolver_code_exe`` y ``entorno_hijo`` de ``tab_launch.py``.
4. Espera ``resultados/<nonce>.json`` hasta ``--timeout`` y lo traduce a un exit code.

Exit: 0 ok · 1 error · 2 timeout · 4 rechazo de la extensión (no_existe, duplicada, ...).
Rastro en ``~/.claude/tab-dispatch/logs/tab_control.log``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tab_launch  # noqa: E402  (se reusa resolver_code_exe, entorno_hijo, ahora_art)

URI_BASE = "vscode://conradorebuffo.claude-tab-dispatch"
ACCIONES = ("list", "close", "focus")
TIMEOUT_DEFAULT_S = 15.0
SONDEO_S = 0.25
ESPERA_CODE_EXE_S = 20.0
ESPERA_MEDIR_S = 5.0
PROCESOS_A_MEDIR = ("claude", "Code")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2
EXIT_RECHAZO = 4

DATA_DIR = Path(os.environ.get("TAB_DISPATCH_HOME") or (Path.home() / ".claude" / "tab-dispatch"))
LOG_PATH = DATA_DIR / "logs" / "tab_control.log"


class TabControlError(Exception):
    """Fallo que termina la corrida con exit 1 y un mensaje legible."""


def base_dir():
    """Carpeta de intercambio con la extensión (la misma que usa ``extension.js``)."""
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return Path(local) / "ClaudeTabDispatch"


def build_uri(accion, titulo, nonce):
    if accion not in ACCIONES:
        raise TabControlError(f"accion invalida: {accion!r}")
    if accion == "list":
        return f"{URI_BASE}/list?nonce={nonce}"
    if not titulo:
        raise TabControlError(f"{accion} necesita el titulo de la pestaña")
    # safe="" codifica '/', ':' y '&'; el espacio va como %20, nunca '+'.
    return f"{URI_BASE}/{accion}?title={quote(titulo, safe='')}&nonce={nonce}"


# ---------------------------------------------------------------- instancia destino

def _norm_ruta(texto):
    return str(texto).strip().strip('"').replace("/", "\\").rstrip("\\").lower()


def lineas_code_exe():
    """Líneas de comando de los procesos ``Code.exe`` (PowerShell + CIM). Lanza TabControlError."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='Code.exe'\" | "
          "Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TabControlError(f"no pude listar procesos Code.exe: {e!r}") from e
    if r.returncode != 0:
        raise TabControlError(f"listar procesos Code.exe fallo (exit {r.returncode}): {r.stderr.strip()[:200]}")
    salida = (r.stdout or "").strip()
    if not salida:
        return []
    datos = json.loads(salida)
    if isinstance(datos, str):
        datos = [datos]
    return [d for d in datos if isinstance(d, str)]


def instancia_corriendo(user_data_dir, lineas):
    """True si hay un proceso PRINCIPAL de Code.exe de esa instancia.

    Los procesos hijos de Electron llevan ``--type=``; el principal no. Sin ``user_data_dir`` la
    instancia destino es la principal de Conrado: un Code.exe principal sin ``--user-data-dir``.
    """
    ud = _norm_ruta(user_data_dir) if user_data_dir else None
    for linea in lineas:
        if "--type=" in linea:
            continue
        if ud is None:
            if "--user-data-dir" not in linea:
                return True
        elif ud in linea.replace("/", "\\").lower():
            return True
    return False


# ---------------------------------------------------------------- lanzamiento y espera

def lanzar(code_exe, uri, user_data_dir=None, espera_s=ESPERA_CODE_EXE_S):
    """``Code.exe [--user-data-dir X] --open-url -- <uri>`` con el entorno limpio de tab_launch.

    No mata a Code.exe si no termina en ``espera_s`` (puede ser la instancia principal).
    """
    argv = [code_exe]
    if user_data_dir:
        argv += ["--user-data-dir", str(user_data_dir)]
    argv += ["--open-url", "--", uri]
    with tempfile.TemporaryFile() as captura:
        try:
            proc = subprocess.Popen(argv, env=tab_launch.entorno_hijo(), stdin=subprocess.DEVNULL,
                                    stdout=captura, stderr=subprocess.STDOUT)
        except OSError as e:
            raise TabControlError(f"no se pudo ejecutar {code_exe}: {e!r}") from e
        try:
            exit_code = proc.wait(timeout=espera_s)
        except subprocess.TimeoutExpired:
            return {"argv": argv, "exit_code": None, "salida": ""}
        captura.seek(0)
        salida = captura.read().decode("utf-8", errors="replace").strip()
    return {"argv": argv, "exit_code": exit_code, "salida": salida}


def esperar_resultado(ruta, timeout_s, sondeo_s=SONDEO_S, reloj=time.monotonic, dormir=time.sleep):
    """Devuelve el dict del resultado o None si vence ``timeout_s``."""
    limite = reloj() + timeout_s
    while True:
        if ruta.is_file():
            try:
                return json.loads(ruta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass  # la extensión escribe a .tmp y renombra: una lectura rota se reintenta
        if reloj() >= limite:
            return None
        dormir(sondeo_s)


def exit_de_resultado(res):
    if res.get("ok"):
        return EXIT_OK
    if res.get("motivo") == "error":
        return EXIT_ERROR
    return EXIT_RECHAZO


# ---------------------------------------------------------------- memoria

def medir_memoria(consultar=None):
    """{proceso: {n, mb}} para claude y Code, con WorkingSet64. Sólo cantidades."""
    consultar = consultar or _consultar_memoria
    return consultar()


def _consultar_memoria():
    ps = ("Get-Process -Name claude,Code -ErrorAction SilentlyContinue | "
          "Group-Object Name | ForEach-Object { [pscustomobject]@{ n=$_.Name; c=$_.Count; "
          "b=($_.Group | Measure-Object WorkingSet64 -Sum).Sum } } | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise TabControlError(f"medir memoria fallo (exit {r.returncode}): {r.stderr.strip()[:200]}")
    texto = (r.stdout or "").strip()
    datos = json.loads(texto) if texto else []
    if isinstance(datos, dict):
        datos = [datos]
    out = {p: {"n": 0, "mb": 0.0} for p in PROCESOS_A_MEDIR}
    for d in datos:
        nombre = str(d.get("n"))
        if nombre in out:
            out[nombre] = {"n": int(d.get("c") or 0), "mb": round((d.get("b") or 0) / 1048576, 1)}
    return out


def delta_memoria(antes, despues):
    return {p: {"n": despues[p]["n"] - antes[p]["n"], "mb": round(despues[p]["mb"] - antes[p]["mb"], 1)}
            for p in antes if p in despues}


# ---------------------------------------------------------------- log

def registrar(evento, **campos):
    """Una línea JSON por evento. Un log que no se puede escribir se avisa por stderr (regla 16)."""
    linea = {"ts": tab_launch.ahora_art(), "evento": evento, **campos}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[tab_control] no pude escribir {LOG_PATH}: {e!r}", file=sys.stderr)


# ---------------------------------------------------------------- orquestación

def _pedido(accion, titulo):
    p = {"accion": accion, "creado": datetime.now(timezone.utc).isoformat()}
    if accion != "list":
        p["title"] = titulo
    return p


def ejecutar(accion, titulo=None, user_data_dir=None, timeout_s=TIMEOUT_DEFAULT_S, medir=False,
             dry_run=False, base=None, code_exe=None, lineas_fn=None, lanzar_fn=None,
             memoria_fn=None, dormir=time.sleep, reloj=time.monotonic, nonce=None,
             espera_medir_s=ESPERA_MEDIR_S):
    """Corre una acción y devuelve ``{"exit": int, "motivo": str, ...}``. Nunca lanza."""
    base = Path(base) if base else base_dir()
    nonce = nonce or str(uuid.uuid4())
    out = {"accion": accion, "title": titulo, "nonce": nonce, "user_data_dir": user_data_dir}
    try:
        uri = build_uri(accion, titulo, nonce)
        out["uri"] = uri
        lineas = (lineas_fn or lineas_code_exe)()
        corriendo = instancia_corriendo(user_data_dir, lineas)
        out["instancia_corriendo"] = corriendo
        if not corriendo:
            out.update(exit=EXIT_ERROR, motivo="instancia_no_corriendo")
            registrar("sin_instancia", **out)
            return out
        if code_exe is None:
            code_exe, fuente = tab_launch.resolver_code_exe()
            out["code_exe_fuente"] = fuente
        out["code_exe"] = code_exe
        if dry_run:
            out.update(exit=EXIT_OK, motivo="dry_run")
            return out

        antes = medir_memoria(memoria_fn) if medir else None
        pedido_path = base / "pedidos" / f"{nonce}.json"
        resultado_path = base / "resultados" / f"{nonce}.json"
        pedido_path.parent.mkdir(parents=True, exist_ok=True)
        pedido_path.write_text(json.dumps(_pedido(accion, titulo), ensure_ascii=False), encoding="utf-8")
        registrar("pedido", accion=accion, title=titulo, nonce=nonce, user_data_dir=user_data_dir)
        try:
            lanz = (lanzar_fn or lanzar)(code_exe, uri, user_data_dir)
            out["lanzamiento_exit"] = lanz.get("exit_code")
            res = esperar_resultado(resultado_path, timeout_s, dormir=dormir, reloj=reloj)
        finally:
            # Un pedido sin consumir no debe quedar en disco (la extensión también lo vence a los 60 s).
            try:
                pedido_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                print(f"[tab_control] no pude borrar el pedido {pedido_path}: {e!r}", file=sys.stderr)
        if res is None:
            out.update(exit=EXIT_TIMEOUT, motivo="timeout")
        else:
            out["resultado"] = res
            out.update(exit=exit_de_resultado(res), motivo=res.get("motivo", "?"))
            try:
                resultado_path.unlink()
            except OSError as e:
                print(f"[tab_control] no pude borrar el resultado {resultado_path}: {e!r}", file=sys.stderr)
        if medir:
            dormir(espera_medir_s)
            despues = medir_memoria(memoria_fn)
            out["memoria"] = {"antes": antes, "despues": despues, "delta": delta_memoria(antes, despues)}
    except TabControlError as e:
        out.update(exit=EXIT_ERROR, motivo="error", detalle=str(e))
    except Exception as e:  # noqa: BLE001 - el log deja el rastro completo (regla 16)
        out.update(exit=EXIT_ERROR, motivo="error", detalle=repr(e))
        registrar("excepcion", accion=accion, title=titulo, traza=traceback.format_exc())
    registrar("fin", **{k: v for k, v in out.items() if k != "resultado"}, ok=out.get("exit") == EXIT_OK)
    return out


def _parser():
    p = argparse.ArgumentParser(prog="tab_control", description=__doc__.split("\n\n")[0])
    p.add_argument("accion", choices=ACCIONES)
    p.add_argument("titulo", nargs="?", help="titulo exacto de la pestaña (close y focus)")
    p.add_argument("--user-data-dir", help="instancia destino; sin esto, la principal")
    p.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT_S)
    p.add_argument("--medir", action="store_true", help="memoria de claude y Code antes y 5 s despues")
    p.add_argument("--dry-run", action="store_true", help="arma la URI y comprueba la instancia; no lanza")
    p.add_argument("--code-exe")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    code_exe = None
    if args.code_exe:
        try:
            code_exe, _ = tab_launch.resolver_code_exe(args.code_exe)
        except tab_launch.TabLaunchError as e:
            print(f"[tab_control] {e}", file=sys.stderr)
            return EXIT_ERROR
    out = ejecutar(args.accion, args.titulo, args.user_data_dir, args.timeout, args.medir,
                   args.dry_run, code_exe=code_exe)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"{out['accion']} {out.get('title') or ''}: {out['motivo']} (exit {out['exit']})")
        if out.get("detalle"):
            print(out["detalle"], file=sys.stderr)
    return out["exit"]


if __name__ == "__main__":
    sys.exit(main())
