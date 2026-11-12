"""Tests de tab_control.py. Sin VS Code real: lanzador y resultado simulados."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tab_control as tc  # noqa: E402


@pytest.fixture(autouse=True)
def log_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "LOG_PATH", tmp_path / "logs" / "tab_control.log")


PRINCIPAL = ['"C:\\VS\\Code.exe"', '"C:\\VS\\Code.exe" --type=renderer --foo']
GLM = ['"C:\\VS\\Code.exe" --user-data-dir C:\\Users\\x\\AppData\\Local\\VSCode-GLM']
FAKE_EXE = "C:\\VS\\Code.exe"


class Extension:
    """Simula la extensión: al lanzar, exige el pedido en disco y escribe el resultado."""

    def __init__(self, base, resultado, escribir=True):
        self.base, self.resultado, self.escribir = base, resultado, escribir
        self.llamadas = []
        self.pedido_visto = None

    def __call__(self, code_exe, uri, user_data_dir=None):
        self.llamadas.append((code_exe, uri, user_data_dir))
        nonce = uri.split("nonce=")[1]
        pedido = self.base / "pedidos" / f"{nonce}.json"
        self.pedido_visto = json.loads(pedido.read_text(encoding="utf-8")) if pedido.is_file() else None
        if self.escribir:
            d = self.base / "resultados"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{nonce}.json").write_text(json.dumps(self.resultado), encoding="utf-8")
        return {"argv": [], "exit_code": 0, "salida": ""}


def correr(tmp_path, ext, accion="close", titulo="S1-Corto", lineas=PRINCIPAL, **kw):
    return tc.ejecutar(accion, titulo, base=tmp_path / "ks", code_exe=FAKE_EXE,
                       lineas_fn=lambda: lineas, lanzar_fn=ext, dormir=lambda s: None,
                       timeout_s=kw.pop("timeout_s", 1.0), **kw)


def test_ok_escribe_pedido_antes_de_lanzar_y_limpia(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "motivo": "ok", "match": "exacto"})
    out = correr(tmp_path, ext)
    assert out["exit"] == tc.EXIT_OK
    assert ext.pedido_visto["accion"] == "close" and ext.pedido_visto["title"] == "S1-Corto"
    assert "creado" in ext.pedido_visto
    assert list((base / "pedidos").glob("*.json")) == []
    assert list((base / "resultados").glob("*.json")) == []
    uri = ext.llamadas[0][1]
    assert uri.startswith("vscode://conradorebuffo.tab-dispatch/close?title=S1-Corto&nonce=")


def test_titulo_con_espacios_y_ampersand_se_codifica(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "motivo": "ok"})
    correr(tmp_path, ext, titulo="Claude Code & mas")
    assert "title=Claude%20Code%20%26%20mas&nonce=" in ext.llamadas[0][1]


@pytest.mark.parametrize("motivo", ["no_existe", "duplicada", "no_soportado: x"])
def test_rechazo_de_la_extension_es_exit_4(tmp_path, motivo):
    ext = Extension(tmp_path / "ks", {"ok": False, "motivo": motivo})
    out = correr(tmp_path, ext)
    assert out["exit"] == tc.EXIT_RECHAZO
    assert out["motivo"] == motivo


def test_error_de_la_extension_es_exit_1(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": False, "motivo": "error", "detalle": "boom"})
    assert correr(tmp_path, ext)["exit"] == tc.EXIT_ERROR


def test_timeout_es_exit_2_y_borra_el_pedido(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {}, escribir=False)
    t = {"v": 0.0}

    def reloj():
        t["v"] += 0.6
        return t["v"]

    out = tc.ejecutar("close", "S1-Corto", base=base, code_exe=FAKE_EXE, lineas_fn=lambda: PRINCIPAL,
                      lanzar_fn=ext, dormir=lambda s: None, reloj=reloj, timeout_s=1.0)
    assert out["exit"] == tc.EXIT_TIMEOUT and out["motivo"] == "timeout"
    assert list((base / "pedidos").glob("*.json")) == []


def test_instancia_no_corriendo_no_lanza_ni_escribe_pedido(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "motivo": "ok"})
    out = correr(tmp_path, ext, lineas=[])
    assert out["exit"] == tc.EXIT_ERROR and out["motivo"] == "instancia_no_corriendo"
    assert ext.llamadas == []
    assert not (base / "pedidos").exists()


def test_solo_hijos_electron_no_cuentan_como_instancia(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "motivo": "ok"})
    out = correr(tmp_path, ext, lineas=['"C:\\VS\\Code.exe" --type=renderer'])
    assert out["motivo"] == "instancia_no_corriendo"


def test_principal_no_se_confunde_con_code_glm(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "motivo": "ok"})
    # Sólo corre code-glm: la instancia principal NO está corriendo.
    assert correr(tmp_path, ext, lineas=GLM)["motivo"] == "instancia_no_corriendo"
    # Con --user-data-dir de code-glm, sí.
    ud = "C:\\Users\\x\\AppData\\Local\\VSCode-GLM"
    out = correr(tmp_path, ext, lineas=GLM, user_data_dir=ud)
    assert out["exit"] == tc.EXIT_OK
    assert ext.llamadas[0][2] == ud


def test_dry_run_no_lanza_ni_escribe(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "motivo": "ok"})
    out = correr(tmp_path, ext, dry_run=True)
    assert out["exit"] == tc.EXIT_OK and out["motivo"] == "dry_run"
    assert ext.llamadas == [] and not (base / "pedidos").exists()


def test_close_sin_titulo_es_error(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "motivo": "ok"})
    out = correr(tmp_path, ext, titulo=None)
    assert out["exit"] == tc.EXIT_ERROR and ext.llamadas == []


def test_list_no_lleva_titulo(tmp_path):
    base = tmp_path / "ks"
    ext = Extension(base, {"ok": True, "motivo": "ok", "tabs": []})
    out = correr(tmp_path, ext, accion="list", titulo=None)
    assert out["exit"] == tc.EXIT_OK
    assert "title" not in ext.pedido_visto
    assert ext.llamadas[0][1].startswith("vscode://conradorebuffo.tab-dispatch/list?nonce=")


def test_medir_calcula_delta(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "motivo": "ok"})
    fotos = iter([
        {"claude": {"n": 5, "mb": 1000.0}, "Code": {"n": 10, "mb": 2000.0}},
        {"claude": {"n": 4, "mb": 680.0}, "Code": {"n": 10, "mb": 1990.5}},
    ])
    esperas = []
    out = tc.ejecutar("close", "S1-Corto", base=tmp_path / "ks", code_exe=FAKE_EXE, lineas_fn=lambda: PRINCIPAL,
                      lanzar_fn=ext, dormir=esperas.append, timeout_s=1.0, medir=True,
                      memoria_fn=lambda: next(fotos))
    assert out["memoria"]["delta"] == {"claude": {"n": -1, "mb": -320.0}, "Code": {"n": 0, "mb": -9.5}}
    assert tc.ESPERA_MEDIR_S in esperas


def test_lanzar_quita_electron_run_as_node_y_pasa_user_data_dir(monkeypatch):
    visto = {}

    class P:
        pid = 1

        def wait(self, timeout=None):
            return 0

    def popen(argv, env=None, **kw):
        visto["argv"], visto["env"] = argv, env
        return P()

    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    monkeypatch.setattr(tc.subprocess, "Popen", popen)
    r = tc.lanzar(FAKE_EXE, "vscode://conradorebuffo.tab-dispatch/list?nonce=n", "C:\\ud")
    assert r["exit_code"] == 0
    assert "ELECTRON_RUN_AS_NODE" not in visto["env"]
    assert visto["argv"] == [FAKE_EXE, "--user-data-dir", "C:\\ud", "--open-url", "--",
                             "vscode://conradorebuffo.tab-dispatch/list?nonce=n"]


def test_lanzar_sin_user_data_dir(monkeypatch):
    visto = {}

    class P:
        pid = 1

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(tc.subprocess, "Popen", lambda argv, **kw: visto.update(argv=argv) or P())
    tc.lanzar(FAKE_EXE, "u")
    assert visto["argv"] == [FAKE_EXE, "--open-url", "--", "u"]


def test_el_log_deja_rastro(tmp_path):
    ext = Extension(tmp_path / "ks", {"ok": True, "motivo": "ok"})
    correr(tmp_path, ext)
    eventos = [json.loads(x)["evento"] for x in tc.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert eventos == ["pedido", "fin"]
