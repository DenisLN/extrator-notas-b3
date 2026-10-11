"""Testes de travaBanco.py (a região crítica das gravações). Precisam de um
Postgres: usam DATABASE_URL do ambiente e não criam nem gravam tabelas, só
pegam e soltam a trava consultiva. Sem DATABASE_URL, são pulados.

    DATABASE_URL=postgresql://... python -m pytest tests/test_trava_banco.py -q

A trava vale por banco: com um process_notas.py gravando no mesmo banco ao
mesmo tempo, os testes só esperam a vez dele. Use uma base de teste."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sem DATABASE_URL")

# Filho que pega a trava, avisa e a segura por N segundos.
SEGURAR = (
    "import os, sys, time\n"
    f"sys.path.insert(0, {str(RAIZ)!r})\n"
    "from sqlalchemy import create_engine\n"
    "from travaBanco import regiao_critica\n"
    "engine = create_engine(os.environ['DATABASE_URL'])\n"
    "with regiao_critica(engine):\n"
    "    print('pegou', flush=True)\n"
    "    time.sleep(float(sys.argv[1]))\n"
)


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(os.environ["DATABASE_URL"])
    yield eng
    eng.dispose()


def _filho_segurando(segundos):
    p = subprocess.Popen([sys.executable, "-c", SEGURAR, str(segundos)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "pegou"
    return p


def test_reentrante_na_mesma_thread(engine):
    from travaBanco import regiao_critica
    with regiao_critica(engine):
        with regiao_critica(engine):   # não espera por si mesma
            pass


def test_outro_processo_espera_a_vez(engine):
    from travaBanco import regiao_critica
    filho = _filho_segurando(3)
    try:
        inicio = time.monotonic()
        with regiao_critica(engine):
            espera = time.monotonic() - inicio
        assert espera >= 2, f"entrou na região crítica com outro processo dentro (esperou {espera:.1f} s)"
    finally:
        filho.wait(timeout=30)


def test_desiste_depois_da_espera_maxima(engine, monkeypatch):
    import travaBanco
    monkeypatch.setattr(travaBanco, "ESPERA_MAXIMA", 1)
    filho = _filho_segurando(10)
    try:
        with pytest.raises(travaBanco.TravaBancoIndisponivel):
            with travaBanco.regiao_critica(engine):
                pass
    finally:
        filho.kill()
        filho.wait(timeout=30)


def test_processo_morto_solta_a_trava(engine):
    from travaBanco import regiao_critica
    filho = _filho_segurando(600)
    filho.kill()              # como o timeout por arquivo do process_notas.py
    filho.wait(timeout=30)
    inicio = time.monotonic()
    with regiao_critica(engine):
        espera = time.monotonic() - inicio
    assert espera < 30, f"a trava de um processo morto demorou {espera:.1f} s para soltar"
