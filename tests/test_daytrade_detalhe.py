"""Testes de extractDayDetalhe.py com uma nota SINTÉTICA (dados fictícios,
gerada aqui com PyMuPDF no layout da BTG). Não precisam de banco nem de PDF
real. Rode com: python -m pytest tests/test_daytrade_detalhe.py -q"""
import sys
from decimal import Decimal
from pathlib import Path

import pymupdf as pymu
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extractDayDetalhe as det

CABECALHO = [(30, "C/V"), (57, "Mercadoria"), (115, "Vencimento"), (181, "Quantidade"),
             (237, "Preço / Ajuste"), (304, "Tipo Negócio"), (379, "Valor Operação / Ajuste"),
             (462, "D/C"), (508, "Taxa Operacional")]
# Posição de cada campo numa linha de operação, como numa nota real da BTG:
# ('l', x0) alinhado à esquerda; ('r', x1) alinhado à direita.
X = [('l', 33), ('l', 47), ('l', 117), ('r', 218), ('l', 239), ('r', 366), ('r', 456), ('l', 466), ('r', 564)]


def _x(alinhamento, texto):
    lado, x = alinhamento
    return x if lado == 'l' else x - pymu.get_text_length(texto, fontsize=7)


def nota_btg(operacoes, valor_negocios, taxa_operacional="0,00"):
    doc = pymu.open()
    page = doc.new_page(width=595, height=842)
    for x, texto in CABECALHO:
        page.insert_text((x, 187), texto, fontsize=7)
    for i, campos in enumerate(operacoes):
        for alinhamento, texto in zip(X, campos):
            if texto:
                page.insert_text((_x(alinhamento, texto), 197 + 10 * i), texto, fontsize=7)
    # resumo (abaixo da região das operações)
    for x, texto in [(27, "Venda disponível"), (136, "Compra disponível"), (245, "Venda Opções"),
                     (354, "Compra Opções"), (463, "Valor dos negócios")]:
        page.insert_text((x, 652), texto, fontsize=7)
    page.insert_text((541, 662), valor_negocios, fontsize=7)
    for x, texto in [(27, "IRRF"), (136, "IRRF Day Trade (proj.)"), (245, "Taxa operacional"),
                     (354, "Taxa registro BM&F"), (463, "Taxas BM&F (emol+f.gar)")]:
        page.insert_text((x, 673), texto, fontsize=7)
    for x, texto in [(118, "0,00"), (227, "0,15"), (336, taxa_operacional), (445, "3,20"), (544, "1,80 | D")]:
        page.insert_text((x, 683), texto, fontsize=7)
    return doc


OPS = [
    ["C", "WINV26", "14/10/2026", "10", "177.230,0000", "DAY TRADE", "2.066,00", "C", "0,00"],
    ["V", "WINV26", "14/10/2026", "10", "177.240,0000", "DAY TRADE", "2.046,00", "D", "0,00"],
    ["V", "WINPPPP", "01/01/0001", "0", "0,00", "TX. PERMANÊNCIA", "0,00", "", "0,00"],
]


def test_extrai_operacoes_e_confere_com_o_resumo():
    doc = nota_btg(OPS, "20,00 | C")
    ops, conf = det.extrair_nota(doc, "BTG", [0])
    assert len(ops) == 3
    assert ops[0]["operacaoTipo"] == "C"
    assert ops[0]["mercadoria"] == "WINV26"
    assert ops[0]["vencimento"].isoformat() == "2026-10-14"
    assert ops[0]["quantidade"] == 10
    assert ops[0]["precoAjuste"] == Decimal("177230.0000")
    assert ops[0]["tipoNegocio"] == "DAY TRADE"
    assert ops[0]["valorOperacao"] == Decimal("2066.00")
    assert ops[0]["debitoCredito"] == "C"
    assert ops[1]["debitoCredito"] == "D"
    assert ops[2]["debitoCredito"] is None          # valor 0,00 vem sem D/C
    assert ops[2]["tipoNegocio"] == "TX. PERMANÊNCIA"
    assert [o["ordem"] for o in ops] == [1, 2, 3]
    assert conf["soma_operacoes"] == conf["valor_negocios"] == Decimal("20.00")


def test_soma_que_nao_bate_e_erro():
    doc = nota_btg(OPS, "21,00 | C")
    with pytest.raises(det.ErroDetalheDaytrade, match="não bate"):
        det.extrair_nota(doc, "BTG", [0])


def test_taxa_operacional_que_nao_bate_e_erro():
    doc = nota_btg(OPS, "20,00 | C", taxa_operacional="4,00")
    with pytest.raises(det.ErroDetalheDaytrade, match="taxa operacional"):
        det.extrair_nota(doc, "BTG", [0])


def test_linha_fora_do_formato_e_erro():
    ruim = OPS[:1] + [["X", "WINV26", "14/10/2026", "10", "177.240,0000", "DAY TRADE", "2.046,00", "D", "0,00"]]
    doc = nota_btg(ruim, "20,00 | C")
    with pytest.raises(det.ErroDetalheDaytrade, match="fora do formato"):
        det.extrair_nota(doc, "BTG", [0])


def test_quantidade_negativa_de_ajuste_de_posicao():
    ops = [["V", "WINV25", "15/10/2025", "-10", "141.884,00", "AJUPOS", "1.212,00", "C", "0,00"]]
    doc = nota_btg(ops, "1.212,00 | C")
    lidas, _ = det.extrair_nota(doc, "BTG", [0])
    assert lidas[0]["quantidade"] == -10
