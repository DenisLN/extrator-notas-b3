"""Testes da normalização do ticker do mercado fracionário (assetMapper).
Não precisam de banco. Rode com: python -m pytest tests/test_ticker_fracionario.py -q"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assetMapper import normalizar_ticker, extract_ticker_from_name, classificar_tipo_ativo


@pytest.mark.parametrize("entrada,esperado", [
    ("PETR3F", "PETR3"), ("PETR4F", "PETR4"), ("CURY3F", "CURY3"),
    ("B3SA3F", "B3SA3"), ("BOVA11F", "BOVA11"), ("KLBN11F", "KLBN11"),
    ("AAPL34F", "AAPL34"), (" petr3f ", "PETR3"),
])
def test_fracionario_perde_o_f(entrada, esperado):
    assert normalizar_ticker(entrada) == esperado


@pytest.mark.parametrize("ticker", [
    "PETR3", "PETR4", "BRCO11", "HGLG11",   # já normais
    "PETRF",        # sem dígito
    "PETR9F",       # 9 não é final de ação
    "ABCDEF",       # nada de número
    "PETR3FF", "XPETR3F",  # tamanho errado
    "FIIF11", "FF11",  # F no meio/raiz
    "",
])
def test_nao_mexe_no_que_nao_e_fracionario(ticker):
    assert normalizar_ticker(ticker) == ticker


def test_none_passa():
    assert normalizar_ticker(None) is None


def test_heuristica_do_nome_bruto():
    assert extract_ticker_from_name("PETR3 ON") == "PETR3"
    assert extract_ticker_from_name("PETR3F ON") == "PETR3"
    assert extract_ticker_from_name("BOVA11F CI") == "BOVA11"


def test_tipo_do_fracionario_normalizado():
    assert classificar_tipo_ativo(normalizar_ticker("HGLG11F")) == "FII"
