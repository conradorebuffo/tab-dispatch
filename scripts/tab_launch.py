# -*- coding: utf-8 -*-
"""tab_launch.py -- Abre una pestaña nueva de Claude Code en VS Code por deep link y espera
hasta que otra sesión pueda escribirle (relevo de sesión).

Flujo:
    1. Foto de los ``sessionId`` vivos (``claude agents --json``).
    2. Lanza ``Code.exe --open-url -- vscode://anthropic.claude-code/open`` con el entorno
       del proceso hijo SIN ``ELECTRON_RUN_AS_NODE``.
    3. Sondea ``claude agents --json`` hasta ver una sesión ``interactive`` que no estaba
       en la foto, o hasta ``--timeout``.
    4. Imprime el resultado (``--json`` para consumirlo desde el Router).

Uso:
    python scripts/tab_launch.py --thread <thread_id> --json
    python scripts/tab_launch.py --dry-run --json

Exit codes:
    0  pestaña detectada
    2  timeout: no apareció ninguna sesión nueva (casi siempre, el diálogo de VS Code
       sigue sin confirmar)
    1  error: no es Windows, Code.exe o ``claude`` no encontrados, Code.exe devolvió un
       exit distinto de 0, dos sesiones nuevas a la vez, argumentos inválidos, etc.
       (argparse usa 2 para errores de uso; acá se fuerza a 1 para no confundirlo con
       el timeout)

HECHOS MEDIDOS en las pruebas previas (2026-11-11):

* F0-i. El Bash/Python de una pestaña de Claude Code hereda ``ELECTRON_RUN_AS_NODE``.
  Con esa variable ``Code.exe`` corre como Node y rechaza el manejador del protocolo:
  ``bad option: --open-url`` (exit 9). ``os.startfile`` dispara el mismo manejador con
  el mismo entorno y falla sin error visible. Por eso acá se ejecuta ``Code.exe`` a
  mano y con la variable quitada del entorno del hijo (el del padre no se toca).
* F0-j. VS Code muestra el diálogo modal "Allow 'Claude Code for VS Code' extension to
  open this URI?" en cada lanzamiento, salvo que el usuario haya tildado "Do not ask me
  again for this extension". Mientras nadie toca "Open" la pestaña no existe: eso es el
  exit 2. Si el diálogo se confirma después del timeout la pestaña se abre igual, así
  que antes de relanzar conviene mirar ``claude agents --json``.
* F0-g. La pestaña abierta por URI figura en ``claude agents --json`` como sesión
  ``interactive`` / ``idle`` con un ``sessionId`` nuevo y un nombre nativo tipo
  ``myproject-b3`` ANTES de recibir ningún prompt. Por eso el default es abrir SIN
  prompt precargado: el Router le manda la primera orden por mensaje. ``--thread`` sólo
  se registra y se devuelve; no precarga nada. ``--prompt`` existe para el caso manual
  (el texto queda escrito en la caja, sin enviar).

La URI admite además un parámetro ``session`` (retomar una sesión existente). Este
script no lo expone: el relevo abre siempre una pestaña nueva.

Cada lanzamiento deja rastro append-only en ``~/.claude/tab-dispatch/logs/tab_launch.log`` (una línea
JSON por evento, con la hora ART tomada del reloj): ``lanzado`` apenas ``Code.exe``
termina, y ``resultado`` al final. Son dos líneas a propósito: si el proceso muere
durante la espera, queda constancia de que hay un diálogo abierto en pantalla.
``--dry-run`` no lanza ni escribe el log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

ART = timezone(timedelta(hours=-3))

URI_BASE = "vscode://anthropic.claude-code/open"

#: Variables que se quitan del entorno del proceso hijo (F0-i). Sólo lo medido.
ENV_A_QUITAR = ("ELECTRON_RUN_AS_NODE",)

#: Comando del protocolo ``vscode`` (HKCR es la vista fusionada de HKCU y HKLM).
REG_SUBCLAVE = r"vscode\shell\open\command"

#: Último recurso si no hay ``--code-exe`` ni registro: la instalación por usuario.
CODE_EXE_ULTIMO_RECURSO = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
    "Programs", "Microsoft VS Code", "Code.exe")

TIMEOUT_DEFAULT_S = 90.0
SONDEO_S = 2.0
#: Con una instancia de VS Code ya abierta, ``Code.exe --open-url`` reenvía la URI y sale
#: enseguida. Si no sale en este plazo NO se lo mata: puede ser la instancia principal.
ESPERA_CODE_EXE_S = 20.0
AGENTS_TIMEOUT_S = 30.0

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2

#: Carpeta de datos del plugin (la misma que usa el libro de ordenes). TAB_DISPATCH_HOME la cambia.
DATA_DIR = Path(os.environ.get("TAB_DISPATCH_HOME") or (Path.home() / ".claude" / "tab-dispatch"))
LOG_PATH = DATA_DIR / "logs" / "tab_launch.log"


class TabLaunchError(Exception):
    """Fallo que termina la corrida con exit 1 y un mensaje legible."""


def _aviso(msg):
    print(f"[tab_launch] {msg}", file=sys.stderr)


def es_windows():
    return sys.platform == "win32"


def ahora_art():
    """Hora ART leída del reloj de la máquina (nunca estimada por un modelo: F0-b)."""
    return datetime.now(ART).strftime("%Y-%m-%d %H:%M:%S ART")


# ---------------------------------------------------------------- URI y entorno

def build_uri(prompt=None):
    """URI del deep link. Sin prompt (default) la pestaña abre con la caja vacía."""
    if prompt is None or not str(prompt).strip():
        return URI_BASE
    # safe="" codifica también '/', ':' y '&'; el espacio va como %20, nunca '+'.
    return f"{URI_BASE}?prompt={quote(str(prompt), safe='')}"


def entorno_hijo(base=None):
    """Copia del entorno sin las variables de ``ENV_A_QUITAR``.

    Compara en mayúsculas: en Windows los nombres de variable no distinguen caja.
    Devuelve SIEMPRE un dict nuevo; el entorno del padre queda intacto.
    """
    origen = os.environ if base is None else base
    quitar = {v.upper() for v in ENV_A_QUITAR}
    return {k: v for k, v in origen.items() if k.upper() not in quitar}


def variables_presentes(base=None):
    """Cuáles de ``ENV_A_QUITAR`` están hoy en el entorno. Sólo nombres, nunca valores."""
    origen = os.environ if base is None else base
    presentes = {k.upper() for k in origen}
    return [v for v in ENV_A_QUITAR if v.upper() in presentes]


# ---------------------------------------------------------------- Code.exe

def exe_de_comando(comando):
    """Extrae el ejecutable de un comando de protocolo del registro.

    ``"C:\\...\\Code.exe" --open-url -- "%1"`` -> ``C:\\...\\Code.exe``. Acepta también la
    forma sin comillas, con espacios en la ruta. Devuelve None si no hay nada que extraer.
    """
    s = (comando or "").strip()
    if not s:
        return None
    if s.startswith('"'):
        fin = s.find('"', 1)
        return s[1:fin] if fin > 1 else None
    m = re.match(r"(.+?\.exe)(?=\s|$)", s, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return s.split()[0]


def leer_comando_registro():
    """Valor por defecto de ``HKCR\\vscode\\shell\\open\\command``, o None.

    Nunca lanza: un registro ilegible deja el motivo en stderr y cede al último recurso.
    """
    try:
        import winreg
    except ImportError as e:
        _aviso(f"registro: winreg no disponible ({e!r})")
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, REG_SUBCLAVE) as clave:
            valor, tipo = winreg.QueryValueEx(clave, "")
            if tipo == winreg.REG_EXPAND_SZ and isinstance(valor, str):
                valor = winreg.ExpandEnvironmentStrings(valor)
    except OSError as e:
        _aviso(f"registro: no se pudo leer HKCR\\{REG_SUBCLAVE} ({e!r})")
        return None
    if not isinstance(valor, str) or not valor.strip():
        _aviso(f"registro: HKCR\\{REG_SUBCLAVE} está vacío")
        return None
    return valor


def candidatos_ultimo_recurso():
    """Rutas a probar cuando no hay ``--code-exe`` ni registro utilizable."""
    rutas = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        rutas.append(os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe"))
    if CODE_EXE_ULTIMO_RECURSO not in rutas:
        rutas.append(CODE_EXE_ULTIMO_RECURSO)
    return rutas


def _es_exe(ruta):
    return str(ruta).lower().endswith(".exe")


def resolver_code_exe(forzado=None):
    """Devuelve ``(ruta, fuente)`` con fuente en {'--code-exe', 'registro', 'ultimo-recurso'}.

    Orden: ``--code-exe`` (manda y no cae a otra vía si está mal) -> comando del protocolo
    ``vscode`` en el registro -> ruta por usuario. Sólo se acepta un ``.exe``: los wrappers
    ``code.cmd``/``.bat`` pasan por ``cmd.exe``, que reinterpreta los ``%XX`` de la URI, y
    lo medido en la Fase 0 es ``Code.exe``.
    """
    if forzado:
        if not _es_exe(forzado):
            raise TabLaunchError(
                f"--code-exe tiene que ser Code.exe, no un wrapper .cmd/.bat: {forzado}")
        if not os.path.isfile(forzado):
            raise TabLaunchError(f"--code-exe no existe: {forzado}")
        return str(forzado), "--code-exe"

    comando = leer_comando_registro()
    if comando:
        exe = exe_de_comando(comando)
        if exe and _es_exe(exe) and os.path.isfile(exe):
            return exe, "registro"
        _aviso(f"registro: el comando del protocolo vscode no apunta a un .exe existente "
               f"({exe!r}); se prueba el último recurso")

    probadas = candidatos_ultimo_recurso()
    for ruta in probadas:
        if os.path.isfile(ruta):
            return ruta, "ultimo-recurso"
    raise TabLaunchError(
        "no se encontró Code.exe: ni en el registro (HKCR\\" + REG_SUBCLAVE + ") ni en "
        + " | ".join(probadas) + ". Pasalo con --code-exe")


# ---------------------------------------------------------------- lanzamiento

def lanzar_uri(code_exe, uri, espera_s=ESPERA_CODE_EXE_S):
    """Ejecuta ``Code.exe --open-url -- <uri>`` con el entorno limpio.

    Devuelve ``{"argv", "exit_code", "salida"}``. ``exit_code`` es None si ``Code.exe`` no
    terminó en ``espera_s``: no se lo mata (puede ser la instancia principal de VS Code) y
    el llamador sigue sondeando.

    La salida va a un archivo temporal y no a un pipe: con pipes, un proceso nieto que
    herede el handle dejaría colgada la lectura aunque ``Code.exe`` ya hubiera salido.
    """
    argv = [code_exe, "--open-url", "--", uri]
    with tempfile.TemporaryFile() as captura:
        try:
            proc = subprocess.Popen(argv, env=entorno_hijo(), stdin=subprocess.DEVNULL,
                                    stdout=captura, stderr=subprocess.STDOUT)
        except OSError as e:
            raise TabLaunchError(f"no se pudo ejecutar {code_exe}: {e!r}") from e
        try:
            exit_code = proc.wait(timeout=espera_s)
        except subprocess.TimeoutExpired:
            _aviso(f"Code.exe (pid {proc.pid}) no terminó en {espera_s:g} s; se lo deja vivo "
                   f"(puede ser la instancia principal de VS Code) y se sigue sondeando")
            return {"argv": argv, "exit_code": None, "salida": ""}
        captura.seek(0)
        salida = captura.read().decode("utf-8", errors="replace").strip()
    return {"argv": argv, "exit_code": exit_code, "salida": salida}


# ---------------------------------------------------------------- claude agents

def parsear_agentes(texto):
    """``claude agents --json`` -> lista de dicts. Acepta lista u objeto con clave ``agents``."""
    try:
        data = json.loads(texto)
    except ValueError as e:
        raise TabLaunchError(f"claude agents --json no devolvió JSON válido ({e})") from e
    if isinstance(data, dict):
        data = data.get("agents")
    if not isinstance(data, list):
        raise TabLaunchError("claude agents --json devolvió una forma inesperada "
                             "(ni lista ni objeto con clave 'agents')")
    return [e for e in data if isinstance(e, dict)]


def listar_agentes(timeout_s=AGENTS_TIMEOUT_S):
    exe = shutil.which("claude")
    if not exe:
        raise TabLaunchError("no se encuentra el ejecutable 'claude' en PATH")
    try:
        r = subprocess.run([exe, "agents", "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                           timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise TabLaunchError(f"claude agents --json no respondió en {timeout_s:g} s") from e
    except OSError as e:
        raise TabLaunchError(f"no se pudo ejecutar claude agents --json: {e!r}") from e
    if r.returncode != 0:
        raise TabLaunchError(f"claude agents --json devolvió exit {r.returncode}: "
                             f"{(r.stderr or '').strip()[:300]}")
    return parsear_agentes(r.stdout)


def ids_vivos(entradas):
    return {str(e["sessionId"]) for e in entradas if e.get("sessionId")}


def _resumen(entrada):
    return {"session_id": str(entrada.get("sessionId") or ""),
            "native_name": entrada.get("name"),
            "status": entrada.get("status")}


def candidatas_nuevas(entradas, foto):
    """Sesiones ``interactive`` cuyo ``sessionId`` no estaba en la foto (sin duplicados)."""
    nuevas = {}
    for e in entradas:
        sid = str(e.get("sessionId") or "")
        if not sid or sid in foto:
            continue
        if str(e.get("kind") or "").lower() != "interactive":
            continue
        nuevas.setdefault(sid, _resumen(e))
    return list(nuevas.values())


def esperar_pestana(foto, timeout_s, t0, sondeo_s=SONDEO_S):
    """Sondea hasta ver exactamente una sesión nueva, más de una, o agotar el plazo.

    Devuelve ``{"estado", "candidatas", "elapsed_s", "sondeos_ok", "sondeos_fallidos",
    "ultimo_error"}`` con estado en {'ok', 'ambiguo', 'timeout', 'sin-datos'}. Un sondeo
    fallido no corta la espera (deja el motivo en stderr); si NINGUNO respondió el estado
    es 'sin-datos' y no 'timeout', para no culpar al diálogo de un ``claude`` roto.
    """
    limite = t0 + timeout_s
    sondeos_ok = sondeos_fallidos = 0
    ultimo_error = None
    estado, nuevas = None, []
    while True:
        try:
            nuevas = candidatas_nuevas(listar_agentes(), foto)
            sondeos_ok += 1
        except TabLaunchError as e:
            nuevas = []
            sondeos_fallidos += 1
            if str(e) != ultimo_error:
                _aviso(f"sondeo fallido: {e}")
            ultimo_error = str(e)
        if len(nuevas) == 1:
            estado = "ok"
            break
        if len(nuevas) > 1:
            estado = "ambiguo"
            break
        restante = limite - time.monotonic()
        if restante <= 0:
            estado = "timeout" if sondeos_ok else "sin-datos"
            break
        time.sleep(min(sondeo_s, restante))
    return {"estado": estado, "candidatas": nuevas,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "sondeos_ok": sondeos_ok, "sondeos_fallidos": sondeos_fallidos,
            "ultimo_error": ultimo_error}


# ---------------------------------------------------------------- log

def registrar(evento, thread_id, uri, **campos):
    """Línea JSON append-only en ``LOG_PATH``. Un fallo de escritura no corta el lanzamiento
    pero queda en stderr (regla 16)."""
    linea = {"ts": ahora_art(), "evento": evento, "thread_id": thread_id, "uri": uri}
    linea.update(campos)
    try:
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except OSError as e:
        _aviso(f"no se pudo escribir el log {LOG_PATH}: {e!r}")


# ---------------------------------------------------------------- CLI

class _Parser(argparse.ArgumentParser):
    """argparse sale con 2 ante un error de uso; acá 2 significa timeout."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def _segundos(valor):
    try:
        n = float(valor)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"no es un número de segundos: {valor!r}") from e
    if not n > 0:
        raise argparse.ArgumentTypeError(f"tiene que ser mayor que 0: {valor!r}")
    return n


def _parser():
    ap = _Parser(
        prog="tab_launch.py",
        description="Abre una pestaña nueva de Claude Code en VS Code por deep link y espera "
                    "a que sea direccionable .",
        epilog="Exit: 0 pestaña detectada | 2 timeout (diálogo de VS Code sin confirmar) | "
               "1 error.")
    ap.add_argument("--thread", default=None, metavar="THREAD_ID",
                    help="hilo que va a retomar la pestaña: se registra en el log y se "
                         "devuelve en la salida. NO precarga ningún prompt")
    ap.add_argument("--prompt", default=None, metavar="TEXTO",
                    help="texto a precargar en la caja (queda escrito, sin enviar). "
                         "Default: sin prompt; la primera orden la manda el Router")
    ap.add_argument("--timeout", type=_segundos, default=TIMEOUT_DEFAULT_S, metavar="S",
                    help=f"segundos de espera a que aparezca la pestaña "
                         f"(default {TIMEOUT_DEFAULT_S:g}; sondeo cada {SONDEO_S:g} s)")
    ap.add_argument("--code-exe", default=None, metavar="RUTA",
                    help="fuerza la ruta de Code.exe (default: registro del protocolo "
                         "vscode, y si no la instalación por usuario)")
    ap.add_argument("--dry-run", action="store_true",
                    help="imprime ejecutable, URI y variables que quita, sin lanzar nada")
    ap.add_argument("--json", action="store_true", help="salida JSON en stdout")
    return ap


def _payload(thread_id, uri, session_id=None, native_name=None, status=None, elapsed_s=None):
    return {"session_id": session_id, "native_name": native_name, "status": status,
            "elapsed_s": elapsed_s, "thread_id": thread_id, "uri": uri}


def _imprimir_json(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _fallar(args, uri, mensaje, exit_code, resultado, lanzado, elapsed_s=None, candidatas=None):
    """Salida común de todo lo que no es exit 0: stderr, JSON opcional y línea de log."""
    print(f"tab_launch: {mensaje}", file=sys.stderr)
    payload = _payload(args.thread, uri, elapsed_s=elapsed_s)
    payload["error"] = mensaje
    if candidatas:
        payload["candidates"] = candidatas
    if args.json:
        _imprimir_json(payload)
    if not args.dry_run:
        extra = {"candidates": candidatas} if candidatas else {}
        registrar("resultado", args.thread, uri, resultado=resultado, exit_code=exit_code,
                  lanzado=lanzado, elapsed_s=elapsed_s, error=mensaje, **extra)
    return exit_code


def _dry_run(args, uri, code_exe, fuente, claude_exe):
    plan = {
        "dry_run": True,
        "code_exe": code_exe,
        "code_exe_source": fuente,
        "argv": [code_exe, "--open-url", "--", uri],
        "uri": uri,
        "env_removed": list(ENV_A_QUITAR),
        "env_removed_present": variables_presentes(),
        "claude_exe": claude_exe,
        "thread_id": args.thread,
        "timeout_s": args.timeout,
        "poll_s": SONDEO_S,
    }
    if args.json:
        _imprimir_json(plan)
        return EXIT_OK
    presentes = set(plan["env_removed_present"])
    print("[dry-run] no se lanza nada ni se escribe el log")
    print(f"ejecutable : {code_exe}  (fuente: {fuente})")
    print(f"argumentos : --open-url -- {uri}")
    print(f"uri        : {uri}")
    for var in ENV_A_QUITAR:
        estado = "presente en este entorno" if var in presentes else "no está en este entorno"
        print(f"quita      : {var}  ({estado})")
    print(f"claude     : {claude_exe}")
    print(f"thread     : {args.thread or '(sin hilo)'}")
    print(f"espera     : {args.timeout:g} s, sondeo cada {SONDEO_S:g} s")
    return EXIT_OK


def main(argv=None):
    args = _parser().parse_args(argv)
    uri = build_uri(args.prompt)
    lanzado = False
    try:
        if not es_windows():
            raise TabLaunchError(
                f"sólo corre en Windows (plataforma: {sys.platform}): lanza Code.exe")
        code_exe, fuente = resolver_code_exe(args.code_exe)
        claude_exe = shutil.which("claude")
        if not claude_exe:
            raise TabLaunchError("no se encuentra el ejecutable 'claude' en PATH: sin él no "
                                 "se puede detectar la pestaña nueva")
        if args.dry_run:
            return _dry_run(args, uri, code_exe, fuente, claude_exe)

        foto = ids_vivos(listar_agentes())
        t0 = time.monotonic()
        lanz = lanzar_uri(code_exe, uri)
        if lanz["exit_code"] not in (0, None):
            raise TabLaunchError(
                f"Code.exe devolvió exit {lanz['exit_code']} (la URI no se abrió): "
                f"{lanz['salida'][:300] or 'sin salida'}")
        lanzado = True
        registrar("lanzado", args.thread, uri, code_exe=code_exe, code_exe_source=fuente,
                  code_exit=lanz["exit_code"], sesiones_previas=len(foto))
        res = esperar_pestana(foto, args.timeout, t0)
    except TabLaunchError as e:
        return _fallar(args, uri, str(e), EXIT_ERROR, "error", lanzado)
    except Exception as e:  # rastro completo en stderr y en el log, nunca en silencio
        traceback.print_exc()
        return _fallar(args, uri, f"error inesperado: {type(e).__name__}: {e}",
                       EXIT_ERROR, "error", lanzado)

    estado, elapsed = res["estado"], res["elapsed_s"]

    if estado == "ok":
        c = res["candidatas"][0]
        payload = _payload(args.thread, uri, c["session_id"], c["native_name"], c["status"],
                           elapsed)
        registrar("resultado", args.thread, uri, resultado="ok", exit_code=EXIT_OK,
                  lanzado=True, session_id=c["session_id"], native_name=c["native_name"],
                  status=c["status"], elapsed_s=elapsed)
        if args.json:
            _imprimir_json(payload)
        else:
            print(f"Pestaña nueva detectada en {elapsed:g} s")
            print(f"  session_id : {c['session_id']}")
            print(f"  native_name: {c['native_name']}")
            print(f"  status     : {c['status']}")
            print(f"  thread_id  : {args.thread or '(sin hilo)'}")
            print(f"  uri        : {uri}")
        return EXIT_OK

    if estado == "ambiguo":
        lista = "; ".join(f"{c['native_name']} ({c['session_id']}, {c['status']})"
                          for c in res["candidatas"])
        return _fallar(
            args, uri,
            f"aparecieron {len(res['candidatas'])} sesiones nuevas a la vez y no se puede "
            f"saber cuál es la lanzada: {lista}",
            EXIT_ERROR, "ambiguo", True, elapsed, res["candidatas"])

    if estado == "sin-datos":
        return _fallar(
            args, uri,
            f"la URI se lanzó pero claude agents --json falló en los "
            f"{res['sondeos_fallidos']} sondeos, así que no se sabe si la pestaña abrió: "
            f"{res['ultimo_error']}",
            EXIT_ERROR, "error", True, elapsed)

    fallidos = (f" ({res['sondeos_fallidos']} sondeos fallaron: {res['ultimo_error']})"
                if res["sondeos_fallidos"] else "")
    return _fallar(
        args, uri,
        f"timeout: no apareció ninguna pestaña nueva en {args.timeout:g} s{fallidos}. "
        "Lo más probable es que el diálogo de VS Code \"Allow 'Claude Code for VS Code' "
        "extension to open this URI?\" siga sin confirmar: falta tocar Open (con \"Do not "
        "ask me again for this extension\" tildado no vuelve a preguntar). Si se confirma "
        "más tarde la pestaña se abre igual: antes de relanzar, mirar claude agents --json.",
        EXIT_TIMEOUT, "timeout", True, elapsed)


if __name__ == "__main__":
    for _flujo in (sys.stdout, sys.stderr):
        if hasattr(_flujo, "reconfigure"):
            _flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
