# -*- coding: utf-8 -*-
"""Tests de tab_launch.py.

NINGUN test lanza Code.exe ni corre `claude agents`: el modulo `subprocess` que ve
tab_launch se reemplaza por un doble, igual que `shutil.which`, el reloj y el registro.
El log va a tmp_path. No se toca la DB ni el entorno real del workspace.
"""
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tab_launch  # noqa: E402

VAR = "ELECTRON_RUN_AS_NODE"
CLAVES_SALIDA = {"session_id", "native_name", "status", "elapsed_s", "thread_id", "uri"}


def _agente(sid, name, status="idle", kind="interactive"):
    return {"pid": 1, "cwd": "c:\\myproject", "kind": kind, "startedAt": 1793826600887,
            "sessionId": sid, "name": name, "status": status}


VIEJA_1 = _agente("11111111-0000-4000-8000-000000000001", "S76-RouterTest", "busy")
VIEJA_2 = _agente("22222222-0000-4000-8000-000000000002", "myproject-5c")
NUEVA = _agente("bbbbbbbb-0000-4000-8000-0000000000b3", "myproject-b3")
NUEVA_2 = _agente("cccccccc-0000-4000-8000-0000000000c0", "myproject-c0")
BASE = [VIEJA_1, VIEJA_2]


class FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode
        self.pid = 4242

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("Code.exe", timeout)
        return self.returncode


class FakeSubprocess:
    """Doble del modulo subprocess: Popen = Code.exe, run = `claude agents --json`.

    `agentes` es la secuencia de respuestas de `claude agents`; la ultima se repite.
    Cada respuesta es una lista/dict (se serializa), un str (stdout crudo), una tupla
    (exit, stdout, stderr) o una excepcion a levantar.
    """
    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT
    DEVNULL = subprocess.DEVNULL
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, agentes, code_exit=0, code_salida="", popen_error=None):
        self.agentes = list(agentes)
        self.code_exit = code_exit
        self.code_salida = code_salida
        self.popen_error = popen_error
        self.eventos = []
        self.popen_calls = []
        self.run_calls = []

    def Popen(self, argv, **kw):
        self.eventos.append("popen")
        self.popen_calls.append({"argv": list(argv), "env": kw.get("env"), "kw": kw})
        if self.popen_error:
            raise self.popen_error
        destino = kw.get("stdout")
        if self.code_salida and hasattr(destino, "write"):
            destino.write(self.code_salida.encode("utf-8"))
        return FakeProc(self.code_exit)

    def run(self, argv, **kw):
        self.eventos.append("run")
        self.run_calls.append(list(argv))
        r = self.agentes[min(len(self.run_calls), len(self.agentes)) - 1]
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, tuple):
            rc, out, err = r
        elif isinstance(r, str):
            rc, out, err = 0, r, ""
        else:
            rc, out, err = 0, json.dumps(r), ""
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeTime:
    """Reloj que solo avanza cuando el codigo duerme: ningun test espera de verdad."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Entorno:
    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.log = tmp_path / "logs" / "tab_launch.log"
        self.code_exe = tmp_path / "vscode" / "Code.exe"
        self.code_exe.parent.mkdir()
        self.code_exe.write_bytes(b"")
        self.reloj = FakeTime()
        self.fake = None
        monkeypatch.setattr(tab_launch, "LOG_PATH", self.log)
        monkeypatch.setattr(tab_launch, "es_windows", lambda: True)
        monkeypatch.setattr(tab_launch, "leer_comando_registro",
                            lambda: f'"{self.code_exe}" --open-url -- "%1"')
        monkeypatch.setattr(tab_launch, "time", self.reloj)
        self.claude("C:\\fake\\npm\\claude.CMD")
        self.subprocess([BASE])

    def claude(self, ruta):
        self.mp.setattr(tab_launch, "shutil", types.SimpleNamespace(which=lambda _n: ruta))

    def subprocess(self, agentes, **kw):
        self.fake = FakeSubprocess(agentes, **kw)
        self.mp.setattr(tab_launch, "subprocess", self.fake)
        return self.fake

    def lineas_log(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    return Entorno(monkeypatch, tmp_path)


def _json_stdout(capsys):
    out = capsys.readouterr()
    return json.loads(out.out), out.err


# ------------------------------------------------------------------ entorno del hijo

def test_entorno_hijo_quita_la_variable_sin_distinguir_caja():
    base = {"Electron_Run_As_Node": "1", "PATH": "x", "ELECTRON_NO_ATTACH_CONSOLE": "1"}
    hijo = tab_launch.entorno_hijo(base)
    assert sorted(hijo) == ["ELECTRON_NO_ATTACH_CONSOLE", "PATH"]
    assert "Electron_Run_As_Node" in base  # el dict de origen no se muta


def test_hijo_sin_electron_run_as_node_aunque_el_padre_la_tenga(entorno, monkeypatch, capsys):
    monkeypatch.setenv(VAR, "1")
    fake = entorno.subprocess([BASE, BASE + [NUEVA]])

    assert tab_launch.main(["--json"]) == 0

    (llamada,) = fake.popen_calls
    env = llamada["env"]
    assert env is not None, "env=None hereda el entorno del padre: es justo el bug de F0-i"
    hijo_la_tiene = any(k.upper() == VAR for k in env)
    assert not hijo_la_tiene
    padre_la_tiene = VAR in os.environ
    assert padre_la_tiene, "el entorno del padre no se toca"
    conserva_el_resto = env.get("PATH") == os.environ.get("PATH")
    assert conserva_el_resto
    assert llamada["argv"] == [str(entorno.code_exe), "--open-url", "--", tab_launch.URI_BASE]


def test_claude_agents_no_se_corre_con_shell(entorno, capsys):
    fake = entorno.subprocess([BASE, BASE + [NUEVA]])
    assert tab_launch.main(["--json"]) == 0
    assert fake.run_calls[0] == ["C:\\fake\\npm\\claude.CMD", "agents", "--json"]
    assert "shell" not in fake.popen_calls[0]["kw"]


# ------------------------------------------------------------------ URI

def test_uri_sin_prompt_es_la_base():
    assert tab_launch.build_uri() == "vscode://anthropic.claude-code/open"
    assert tab_launch.build_uri("") == tab_launch.URI_BASE
    assert tab_launch.build_uri("   ") == tab_launch.URI_BASE


def test_uri_urlencodea_el_prompt():
    prompt = "/session-start-fast hilo:thread-x & más=ñ #1 50%+a/b?c\n\"fin\""
    uri = tab_launch.build_uri(prompt)
    assert uri.startswith("vscode://anthropic.claude-code/open?prompt=")
    crudo = uri.split("?prompt=", 1)[1]
    # Solo caracteres no reservados y escapes: ningun separador de URI sin codificar.
    assert re.fullmatch(r"[A-Za-z0-9._~%-]+", crudo), crudo
    assert "%20" in crudo and "+" not in crudo  # espacio como %20, nunca '+'
    assert unquote(crudo) == prompt
    assert parse_qs(urlsplit(uri).query, keep_blank_values=True)["prompt"] == [prompt]


def test_uri_del_ejemplo_del_plan():
    uri = tab_launch.build_uri("/session-start-fast hilo:thread-demo")
    assert uri == ("vscode://anthropic.claude-code/open"
                   "?prompt=%2Fsession-start-fast%20hilo%3Athread-demo")


def test_prompt_llega_codificado_a_code_exe(entorno, capsys):
    fake = entorno.subprocess([BASE, BASE + [NUEVA]])
    assert tab_launch.main(["--prompt", "hola mundo & más", "--json"]) == 0
    payload, _ = _json_stdout(capsys)
    esperado = tab_launch.URI_BASE + "?prompt=hola%20mundo%20%26%20m%C3%A1s"
    assert fake.popen_calls[0]["argv"][-1] == esperado
    assert payload["uri"] == esperado


def test_thread_no_precarga_prompt(entorno, capsys):
    fake = entorno.subprocess([BASE, BASE + [NUEVA]])
    assert tab_launch.main(["--thread", "thread-demo", "--json"]) == 0
    payload, _ = _json_stdout(capsys)
    assert fake.popen_calls[0]["argv"][-1] == tab_launch.URI_BASE
    assert "prompt" not in payload["uri"]
    assert payload["thread_id"] == "thread-demo"


# ------------------------------------------------------------------ deteccion

def test_detecta_la_sesion_nueva(entorno, capsys):
    # foto, sondeo a t=0, sondeo a t=2, sondeo a t=4 (aparece)
    fake = entorno.subprocess([BASE, BASE, BASE, BASE + [NUEVA]])

    rc = tab_launch.main(["--thread", "thread-demo", "--json"])

    payload, _ = _json_stdout(capsys)
    assert rc == 0
    assert set(payload) == CLAVES_SALIDA
    assert payload == {"session_id": NUEVA["sessionId"], "native_name": "myproject-b3",
                       "status": "idle", "elapsed_s": 4.0, "thread_id": "thread-demo",
                       "uri": tab_launch.URI_BASE}
    assert entorno.reloj.sleeps == [2.0, 2.0]
    assert len(fake.popen_calls) == 1
    # La foto se saca ANTES de lanzar: si no, la pestaña nueva caeria adentro de la foto.
    assert fake.eventos[:2] == ["run", "popen"]


def test_salida_humana_sin_json(entorno, capsys):
    entorno.subprocess([BASE, BASE + [NUEVA]])
    assert tab_launch.main(["--thread", "thread-demo"]) == 0
    out = capsys.readouterr().out
    assert NUEVA["sessionId"] in out and "myproject-b3" in out and "thread-demo" in out


def test_acepta_objeto_con_clave_agents(entorno, capsys):
    entorno.subprocess([{"agents": BASE}, {"agents": BASE + [NUEVA]}])
    assert tab_launch.main(["--json"]) == 0
    payload, _ = _json_stdout(capsys)
    assert payload["session_id"] == NUEVA["sessionId"]


def test_sesion_nueva_no_interactiva_no_cuenta(entorno, capsys):
    fondo = _agente("dddddddd-0000-4000-8000-00000000000d", "worker-x", kind="background")
    entorno.subprocess([BASE, BASE + [fondo], BASE + [fondo, NUEVA]])
    assert tab_launch.main(["--json"]) == 0
    payload, _ = _json_stdout(capsys)
    assert payload["session_id"] == NUEVA["sessionId"]


def test_sesion_que_ya_estaba_en_la_foto_no_cuenta(entorno, capsys):
    entorno.subprocess([BASE + [NUEVA]])  # ya vivia antes del lanzamiento
    assert tab_launch.main(["--timeout", "4", "--json"]) == 2


def test_sondeo_fallido_transitorio_no_corta_la_espera(entorno, capsys):
    entorno.subprocess([BASE, (1, "", "boom transitorio"), BASE + [NUEVA]])
    assert tab_launch.main(["--json"]) == 0
    payload, err = _json_stdout(capsys)
    assert payload["session_id"] == NUEVA["sessionId"]
    assert "boom transitorio" in err  # el fallo deja rastro (regla 16)


def test_code_exe_que_no_termina_no_se_mata_y_se_sigue_sondeando(entorno, capsys):
    entorno.subprocess([BASE, BASE + [NUEVA]], code_exit=None)
    assert tab_launch.main(["--json"]) == 0
    payload, err = _json_stdout(capsys)
    assert payload["session_id"] == NUEVA["sessionId"]
    assert "no terminó" in err


# ------------------------------------------------------------------ timeout

def test_timeout_devuelve_exit_2_y_nombra_el_dialogo(entorno, capsys):
    fake = entorno.subprocess([BASE])

    rc = tab_launch.main(["--timeout", "10", "--thread", "thread-demo", "--json"])

    payload, err = _json_stdout(capsys)
    assert rc == 2
    assert "diálogo de VS Code" in err and "Open" in err
    assert "diálogo de VS Code" in payload["error"]
    assert payload["session_id"] is None and payload["thread_id"] == "thread-demo"
    assert payload["elapsed_s"] == 10.0
    assert entorno.reloj.sleeps == [2.0] * 5          # sondeos en t=0,2,4,6,8,10
    assert len(fake.run_calls) == 1 + 6                # foto + 6 sondeos
    assert len(fake.popen_calls) == 1                  # un solo lanzamiento, sin reintento
    assert entorno.lineas_log()[-1]["resultado"] == "timeout"


def test_timeout_no_multiplo_del_sondeo_no_se_pasa_del_plazo(entorno, capsys):
    entorno.subprocess([BASE])
    assert tab_launch.main(["--timeout", "5", "--json"]) == 2
    assert entorno.reloj.sleeps == [2.0, 2.0, 1.0]


def test_si_ningun_sondeo_responde_es_error_y_no_timeout(entorno, capsys):
    entorno.subprocess([BASE, (1, "", "claude roto")])
    rc = tab_launch.main(["--timeout", "4", "--json"])
    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert "claude roto" in payload["error"]
    assert "diálogo" not in payload["error"]


# ------------------------------------------------------------------ dos candidatas

def test_dos_candidatas_devuelve_exit_1_y_las_lista(entorno, capsys):
    entorno.subprocess([BASE, BASE + [NUEVA, NUEVA_2]])

    rc = tab_launch.main(["--json"])

    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert payload["session_id"] is None, "no adivina cual es"
    assert [c["session_id"] for c in payload["candidates"]] == [NUEVA["sessionId"],
                                                               NUEVA_2["sessionId"]]
    assert [c["native_name"] for c in payload["candidates"]] == ["myproject-b3",
                                                                "myproject-c0"]
    for c in (NUEVA, NUEVA_2):
        assert c["sessionId"] in err and c["name"] in err
    assert entorno.lineas_log()[-1]["resultado"] == "ambiguo"


# ------------------------------------------------------------------ dry-run

def test_dry_run_no_lanza_nada(entorno, monkeypatch, capsys):
    monkeypatch.setenv(VAR, "valor-centinela-xyz")
    fake = entorno.subprocess([BASE, BASE + [NUEVA]])

    rc = tab_launch.main(["--dry-run", "--thread", "thread-demo", "--json"])

    out = capsys.readouterr().out
    plan = json.loads(out)
    assert rc == 0
    assert fake.eventos == [], "ni Code.exe ni claude agents"
    assert not entorno.log.exists(), "el dry-run no escribe el log"
    assert plan["dry_run"] is True
    assert plan["code_exe"] == str(entorno.code_exe) and plan["code_exe_source"] == "registro"
    assert plan["argv"] == [str(entorno.code_exe), "--open-url", "--", tab_launch.URI_BASE]
    assert plan["uri"] == tab_launch.URI_BASE
    assert plan["env_removed"] == [VAR] and plan["env_removed_present"] == [VAR]
    assert plan["thread_id"] == "thread-demo"
    assert "valor-centinela-xyz" not in out, "solo la presencia de la variable, nunca su valor"


def test_dry_run_humano_muestra_ejecutable_uri_y_variable(entorno, monkeypatch, capsys):
    monkeypatch.delenv(VAR, raising=False)
    fake = entorno.subprocess([BASE])
    assert tab_launch.main(["--dry-run", "--prompt", "hola mundo"]) == 0
    out = capsys.readouterr().out
    assert str(entorno.code_exe) in out
    assert tab_launch.URI_BASE + "?prompt=hola%20mundo" in out
    assert VAR in out and "no está en este entorno" in out
    assert fake.eventos == []


# ------------------------------------------------------------------ resolucion de Code.exe

def test_exe_de_comando():
    f = tab_launch.exe_de_comando
    ruta = "C:\\Users\\x\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe"
    assert f(f'"{ruta}" --open-url -- "%1"') == ruta
    assert f(f'{ruta} --open-url -- "%1"') == ruta      # sin comillas y con espacios
    assert f("C:\\vsc\\CODE.EXE") == "C:\\vsc\\CODE.EXE"
    assert f("") is None and f(None) is None and f('"') is None


def test_code_exe_forzado_gana_al_registro(entorno, tmp_path):
    otro = tmp_path / "otro" / "Code.exe"
    otro.parent.mkdir()
    otro.write_bytes(b"")
    assert tab_launch.resolver_code_exe(str(otro)) == (str(otro), "--code-exe")


def test_code_exe_forzado_inexistente_no_cae_al_registro(entorno, tmp_path):
    with pytest.raises(tab_launch.TabLaunchError, match="no existe"):
        tab_launch.resolver_code_exe(str(tmp_path / "nada" / "Code.exe"))


def test_code_exe_forzado_rechaza_wrapper_cmd(entorno, tmp_path):
    wrapper = tmp_path / "code.cmd"
    wrapper.write_text("@echo off\n", encoding="utf-8")
    with pytest.raises(tab_launch.TabLaunchError, match="wrapper"):
        tab_launch.resolver_code_exe(str(wrapper))


def test_code_exe_sale_del_registro(entorno):
    assert tab_launch.resolver_code_exe() == (str(entorno.code_exe), "registro")


def test_sin_registro_usa_el_ultimo_recurso(entorno, monkeypatch, tmp_path):
    respaldo = tmp_path / "respaldo" / "Code.exe"
    respaldo.parent.mkdir()
    respaldo.write_bytes(b"")
    monkeypatch.setattr(tab_launch, "leer_comando_registro", lambda: None)
    monkeypatch.setattr(tab_launch, "candidatos_ultimo_recurso",
                        lambda: [str(tmp_path / "no" / "Code.exe"), str(respaldo)])
    assert tab_launch.resolver_code_exe() == (str(respaldo), "ultimo-recurso")


def test_registro_que_apunta_a_un_exe_borrado_cae_al_ultimo_recurso(entorno, monkeypatch,
                                                                   tmp_path, capsys):
    respaldo = tmp_path / "respaldo" / "Code.exe"
    respaldo.parent.mkdir()
    respaldo.write_bytes(b"")
    monkeypatch.setattr(tab_launch, "leer_comando_registro",
                        lambda: '"C:\\ya\\no\\existe\\Code.exe" --open-url -- "%1"')
    monkeypatch.setattr(tab_launch, "candidatos_ultimo_recurso", lambda: [str(respaldo)])
    assert tab_launch.resolver_code_exe() == (str(respaldo), "ultimo-recurso")
    assert "registro" in capsys.readouterr().err  # el descarte deja rastro


def test_ultimo_recurso_incluye_la_ruta_conocida(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "D:\\otro\\Local")
    rutas = tab_launch.candidatos_ultimo_recurso()
    assert rutas[0] == os.path.join("D:\\otro\\Local", "Programs", "Microsoft VS Code",
                                    "Code.exe")
    assert rutas[-1] == tab_launch.CODE_EXE_ULTIMO_RECURSO


def test_code_exe_no_encontrado_devuelve_exit_1_sin_lanzar(entorno, monkeypatch, capsys):
    monkeypatch.setattr(tab_launch, "leer_comando_registro", lambda: None)
    monkeypatch.setattr(tab_launch, "candidatos_ultimo_recurso", lambda: ["C:\\no\\Code.exe"])
    rc = tab_launch.main(["--json"])
    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert "Code.exe" in payload["error"] and "--code-exe" in payload["error"]
    assert entorno.fake.eventos == []


# ------------------------------------------------------------------ otros errores

def test_code_exe_con_exit_distinto_de_cero_devuelve_exit_1(entorno, capsys):
    fake = entorno.subprocess([BASE, BASE + [NUEVA]], code_exit=9,
                              code_salida="Code.exe: bad option: --open-url\n")
    rc = tab_launch.main(["--json"])
    payload, err = _json_stdout(capsys)
    assert rc == 1
    assert "exit 9" in payload["error"] and "bad option: --open-url" in payload["error"]
    assert len(fake.run_calls) == 1, "solo la foto: con la URI sin abrir no hay nada que sondear"
    ultima = entorno.lineas_log()[-1]
    assert ultima["resultado"] == "error" and ultima["lanzado"] is False


def test_code_exe_que_no_se_puede_ejecutar_devuelve_exit_1(entorno, capsys):
    entorno.subprocess([BASE], popen_error=OSError(5, "Acceso denegado"))
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "no se pudo ejecutar" in payload["error"]


def test_claude_no_encontrado_devuelve_exit_1_sin_lanzar(entorno, capsys):
    entorno.claude(None)
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "claude" in payload["error"]
    assert entorno.fake.eventos == []


def test_fuera_de_windows_devuelve_exit_1_sin_lanzar(entorno, monkeypatch, capsys):
    monkeypatch.setattr(tab_launch, "es_windows", lambda: False)
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "Windows" in payload["error"]
    assert entorno.fake.eventos == []


def test_si_falla_la_foto_inicial_no_se_lanza(entorno, capsys):
    fake = entorno.subprocess([(1, "", "sin sesion")])
    rc = tab_launch.main(["--json"])
    payload, _ = _json_stdout(capsys)
    assert rc == 1 and "sin sesion" in payload["error"]
    assert fake.popen_calls == [], "sin foto no se puede distinguir la pestaña nueva"


def test_json_invalido_de_claude_agents_es_error(entorno, capsys):
    fake = entorno.subprocess(["esto no es json"])
    assert tab_launch.main(["--json"]) == 1
    assert fake.popen_calls == []


def test_error_de_uso_sale_con_1_y_no_con_2(entorno, capsys):
    # argparse usa 2 para errores de uso; aca 2 es "timeout" y el Router lo leeria mal.
    for argv in (["--timeout", "abc"], ["--timeout", "0"], ["--opcion-inexistente"]):
        with pytest.raises(SystemExit) as exc:
            tab_launch.main(argv)
        assert exc.value.code == 1, argv
    assert entorno.fake.eventos == []


# ------------------------------------------------------------------ log

def test_log_es_append_only_con_hora_art_thread_uri_y_resultado(entorno, capsys):
    entorno.subprocess([BASE, BASE + [NUEVA]])
    assert tab_launch.main(["--thread", "thread-uno", "--json"]) == 0
    primeras = entorno.lineas_log()
    assert [x["evento"] for x in primeras] == ["lanzado", "resultado"]

    entorno.subprocess([BASE])
    assert tab_launch.main(["--thread", "thread-dos", "--timeout", "2", "--json"]) == 2
    todas = entorno.lineas_log()
    assert todas[:2] == primeras, "las lineas viejas no se reescriben"
    assert [x["evento"] for x in todas] == ["lanzado", "resultado", "lanzado", "resultado"]

    for linea in todas:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ART", linea["ts"])
        assert linea["uri"] == tab_launch.URI_BASE
    assert [x["thread_id"] for x in todas] == ["thread-uno"] * 2 + ["thread-dos"] * 2
    ok, vencido = todas[1], todas[3]
    assert ok["resultado"] == "ok" and ok["session_id"] == NUEVA["sessionId"]
    assert ok["native_name"] == "myproject-b3" and ok["exit_code"] == 0
    assert vencido["resultado"] == "timeout" and vencido["exit_code"] == 2


def test_log_que_no_se_puede_escribir_no_corta_el_lanzamiento(entorno, monkeypatch, capsys,
                                                             tmp_path):
    bloqueo = tmp_path / "soy_un_archivo"
    bloqueo.write_text("x", encoding="utf-8")
    monkeypatch.setattr(tab_launch, "LOG_PATH", bloqueo / "tab_launch.log")
    entorno.subprocess([BASE, BASE + [NUEVA]])
    assert tab_launch.main(["--json"]) == 0
    payload, err = _json_stdout(capsys)
    assert payload["session_id"] == NUEVA["sessionId"]
    assert "no se pudo escribir el log" in err


def test_hora_art_sale_del_reloj():
    from datetime import datetime, timedelta, timezone
    antes = datetime.now(timezone.utc)
    sello = tab_launch.ahora_art()
    assert sello.endswith(" ART")
    leido = datetime.strptime(sello[:-4], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone(timedelta(hours=-3)))
    assert abs((leido - antes).total_seconds()) < 5
