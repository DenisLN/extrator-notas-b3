"""Extração por operação das notas de Day Trade (BM&F) da XP e da BTG.

extractDay.py lê só os totais da nota (líquido, semi-líquido, IRRF) para
notasDaytrade. Este módulo lê, além disso, a tabela de negócios -- uma linha
por operação -- e grava em operacoesdaytrade_detalhe, ligada à nota.

ATENÇÃO: essas operações são extraídas mas AINDA NÃO SÃO USADAS em nenhum
cálculo (ver NOTICE.md). Nada aqui altera notasDaytrade nem as outras tabelas.

Estratégia (a mesma de extractSwing.py): palavras da região (PyMuPDF,
get_text("words")), ordenadas por y e x; linhas agrupadas por proximidade em
y; colunas por posição em x, a partir das palavras do cabeçalho da tabela.
Cada palavra vai para a coluna cujo intervalo contém o seu centro -- os
números são alinhados à direita e o primeiro caractere de "Mercadoria" na XP
fica à esquerda do título da coluna, então o x0 sozinho erraria a coluna.

Conferência: a soma das operações com sinal (C = +, D = -) tem que bater com
"Valor dos negócios" do resumo da nota, e a soma da taxa operacional com
"Taxa operacional". Se não bater, nada é gravado e a nota é registrada como
falha de detalhe.
"""
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pymupdf as pymu
from sqlmodel import Session, select, delete

from areaCodes import areaDict_btg_day_operacoes, areaDict_xp_day_operacoes
from schemas import notasDaytrade, operacoesDaytradeDetalhe

log = logging.getLogger(__name__)

REGIOES = {
    'BTG': areaDict_btg_day_operacoes['tableAreaOperacoes'],
    'XP': areaDict_xp_day_operacoes['tableAreaOperacoes'],
}

# (campo, prefixos aceitos para a 1ª palavra do título da coluna), na ordem
# em que as colunas aparecem. BTG: "Preço / Ajuste", "Valor Operação / Ajuste";
# XP: "Preço/Ajuste", "Vlr de Operação/Ajuste".
COLUNAS = [
    ('operacaoTipo', ('C/V',)),
    ('mercadoria', ('MERCADORIA',)),
    ('vencimento', ('VENCIMENTO',)),
    ('quantidade', ('QUANTIDADE',)),
    ('precoAjuste', ('PREÇO',)),
    ('tipoNegocio', ('TIPO',)),
    ('valorOperacao', ('VALOR', 'VLR')),
    ('debitoCredito', ('D/C',)),
    ('taxaOperacional', ('TAXA',)),
]

Y_TOLERANCIA = 3.0
MARCADORES = '@#*'
# Número pt-BR, com sinal opcional (a quantidade de um AJUPOS vem negativa, ex. '-10').
NUMERO_BR = re.compile(r'^-?(\d{1,3}(\.\d{3})*|\d+)(,\d+)?$')


class ErroDetalheDaytrade(Exception):
    """Falha na leitura ou na conferência da tabela de operações."""


def _agrupar_linhas(words):
    """Agrupa palavras em linhas: ordena por (y0, x0) e abre linha nova quando
    o y0 se afasta mais que Y_TOLERANCIA do y0 da 1ª palavra da linha."""
    linhas = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if linhas and abs(w[1] - linhas[-1][0][1]) <= Y_TOLERANCIA:
            linhas[-1].append(w)
        else:
            linhas.append([w])
    return [sorted(ln, key=lambda w: w[0]) for ln in linhas]


def _norm(texto):
    return re.sub(r'\s+', '', texto.upper())


def _colunas_pelo_cabecalho(linha):
    """Recebe as palavras da linha de cabeçalho e devolve os limites em x
    entre colunas: limites[i] separa a coluna i da i+1 (ponto médio entre o
    fim do título i e o começo do título i+1)."""
    inicios = []
    busca = 0
    for campo, prefixos in COLUNAS:
        for i in range(busca, len(linha)):
            if any(_norm(linha[i][4]).startswith(p) for p in prefixos):
                inicios.append(i)
                busca = i + 1
                break
        else:
            raise ErroDetalheDaytrade(f"cabeçalho da tabela sem a coluna '{campo}' "
                                      f"(lido: {' '.join(w[4] for w in linha)!r})")
    limites = []
    for k in range(len(inicios) - 1):
        fim_titulo = linha[inicios[k + 1] - 1][2]   # x1 da última palavra do título k
        inicio_prox = linha[inicios[k + 1]][0]
        limites.append((fim_titulo + inicio_prox) / 2)
    return limites


def _coluna_da_palavra(w, limites):
    centro = (w[0] + w[2]) / 2
    for i, lim in enumerate(limites):
        if centro < lim:
            return i
    return len(limites)


def _decimal_br(texto, campo):
    t = texto.strip()
    if not NUMERO_BR.match(t):
        raise ErroDetalheDaytrade(f"valor inválido em '{campo}': {texto!r}")
    try:
        return Decimal(t.replace('.', '').replace(',', '.'))
    except InvalidOperation as e:
        raise ErroDetalheDaytrade(f"valor inválido em '{campo}': {texto!r}") from e


def _tirar_marcadores(texto):
    marc = ''.join(c for c in texto if c in MARCADORES)
    limpo = ''.join(c for c in texto if c not in MARCADORES).strip()
    return limpo, marc


def _linha_para_operacao(celulas, pagina, ordem):
    marcadores = ''
    limpas = {}
    for campo, _ in COLUNAS:
        limpo, marc = _tirar_marcadores(celulas.get(campo, ''))
        limpas[campo] = limpo
        marcadores += marc

    venc = limpas['vencimento']
    vencimento = None
    if venc:
        try:
            vencimento = datetime.strptime(venc, '%d/%m/%Y').date()
        except ValueError as e:
            raise ErroDetalheDaytrade(f"vencimento inválido: {venc!r}") from e

    quantidade = _decimal_br(limpas['quantidade'], 'quantidade')
    if quantidade != quantidade.to_integral_value():
        raise ErroDetalheDaytrade(f"quantidade não inteira: {limpas['quantidade']!r}")

    return {
        'pagina': pagina,
        'ordem': ordem,
        'operacaoTipo': limpas['operacaoTipo'],
        'mercadoria': limpas['mercadoria'].replace(' ', ''),
        'vencimento': vencimento,
        'quantidade': int(quantidade),
        'precoAjuste': _decimal_br(limpas['precoAjuste'], 'precoAjuste'),
        'tipoNegocio': ' '.join(limpas['tipoNegocio'].split()),
        'valorOperacao': _decimal_br(limpas['valorOperacao'], 'valorOperacao'),
        'debitoCredito': limpas['debitoCredito'] or None,
        'taxaOperacional': _decimal_br(limpas['taxaOperacional'], 'taxaOperacional'),
        'marcadores': ''.join(sorted(set(marcadores))) or None,
    }


def extrair_operacoes_pagina(page, broker, pagina, ordem_inicial=1):
    """Lê a tabela de operações de uma página. Devolve a lista de operações
    (dicts com os campos de operacoesDaytradeDetalhe, menos os da nota).
    Página sem nenhuma palavra na região devolve lista vazia."""
    regiao = REGIOES[broker]
    words = page.get_text("words", clip=regiao)
    if not words:
        return []
    linhas = _agrupar_linhas(words)

    idx_cab = next((i for i, ln in enumerate(linhas)
                    if _norm(ln[0][4]) == 'C/V' and any(_norm(w[4]).startswith('MERCADORIA') for w in ln)),
                   None)
    if idx_cab is None:
        raise ErroDetalheDaytrade(f"página {pagina}: cabeçalho da tabela de operações (C/V, Mercadoria...) "
                                  f"não encontrado na região {tuple(regiao)}")
    limites = _colunas_pelo_cabecalho(linhas[idx_cab])

    operacoes = []
    for ln in linhas[idx_cab + 1:]:
        celulas = {}
        for w in ln:
            campo = COLUNAS[_coluna_da_palavra(w, limites)][0]
            celulas[campo] = (celulas.get(campo, '') + ' ' + w[4]).strip()
        cv = _tirar_marcadores(celulas.get('operacaoTipo', ''))[0]
        dc = _tirar_marcadores(celulas.get('debitoCredito', ''))[0]
        valor = _tirar_marcadores(celulas.get('valorOperacao', ''))[0]
        # Operação de valor 0,00 vem sem D/C (ex. 'TX. PERMANÊNCIA', ou um
        # day trade zerado no mesmo preço).
        dc_ok = dc in ('C', 'D') or (dc == '' and valor in ('0,00', '0'))
        if cv not in ('C', 'V') or not dc_ok:
            texto = ' '.join(w[4] for w in ln)
            raise ErroDetalheDaytrade(f"página {pagina}: linha fora do formato de operação "
                                      f"(C/V={cv!r}, D/C={dc!r}): {texto!r}")
        try:
            operacoes.append(_linha_para_operacao(celulas, pagina, ordem_inicial + len(operacoes)))
        except ErroDetalheDaytrade as e:
            raise ErroDetalheDaytrade(f"página {pagina}, operação {ordem_inicial + len(operacoes)}: {e}") from e
    return operacoes


def _valor_do_resumo(page, rotulo, y_min):
    """Lê do resumo da nota (abaixo de y_min) o valor sob o rótulo dado, ex.
    'Valor dos negócios'. Devolve (Decimal, 'C'|'D'|None) ou None se o rótulo
    não estiver na página."""
    tokens = rotulo.split()
    linhas = _agrupar_linhas(page.get_text("words", clip=pymu.Rect(0, y_min, page.rect.width, page.rect.height)))
    for i, ln in enumerate(linhas):
        textos = [w[4] for w in ln]
        for j in range(len(textos) - len(tokens) + 1):
            if textos[j:j + len(tokens)] != tokens:
                continue
            x_ini = ln[j][0]
            x_fim_rotulo = ln[j + len(tokens) - 1][2]
            # o próximo rótulo da mesma linha (bem afastado) fecha a coluna
            prox = [w[0] for w in ln[j + len(tokens):] if w[0] > x_fim_rotulo + 10]
            x_fim = prox[0] if prox else page.rect.width
            if i + 1 >= len(linhas):
                return None
            valores = [w[4] for w in linhas[i + 1] if x_ini - 5 <= w[0] < x_fim]
            texto = ' '.join(valores).replace('|', ' ').split()
            if not texto:
                return None
            sinal = texto[-1] if texto[-1] in ('C', 'D') else None
            numero = texto[-2] if sinal else texto[-1]
            return _decimal_br(numero, rotulo), sinal
    return None


def extrair_nota(doc, broker, paginas):
    """Lê as operações das páginas (índices 0-based de doc) de uma nota e
    confere com o resumo da última página. Devolve (operacoes, conferencia)."""
    operacoes = []
    for p in paginas:
        operacoes += extrair_operacoes_pagina(doc[p], broker, p + 1, len(operacoes) + 1)

    ultima = doc[paginas[-1]]
    y_resumo = REGIOES[broker][3]
    vn = _valor_do_resumo(ultima, 'Valor dos negócios', y_resumo)
    taxa = _valor_do_resumo(ultima, 'Taxa operacional', y_resumo)
    if vn is None or taxa is None:
        raise ErroDetalheDaytrade(f"página {paginas[-1] + 1}: resumo da nota sem "
                                  f"'Valor dos negócios' ou 'Taxa operacional'")

    soma = sum((op['valorOperacao'] if op['debitoCredito'] == 'C' else -op['valorOperacao'])
               for op in operacoes)
    soma_taxa = sum(op['taxaOperacional'] for op in operacoes)
    vn_assinado = -vn[0] if vn[1] == 'D' else vn[0]
    conferencia = {
        'operacoes': len(operacoes),
        'soma_operacoes': soma,
        'valor_negocios': vn_assinado,
        'soma_taxa_operacional': soma_taxa,
        'taxa_operacional': taxa[0],
    }
    if soma != vn_assinado or soma_taxa != taxa[0]:
        raise ErroDetalheDaytrade(
            f"soma das {len(operacoes)} operações não bate com o resumo da nota: "
            f"operações {soma} x 'Valor dos negócios' {vn_assinado}; "
            f"taxa operacional {soma_taxa} x {taxa[0]}")
    return operacoes, conferencia


def gravar_detalhe(session: Session, nota: notasDaytrade, operacoes, hash_nota):
    """Substitui as operações da nota em operacoesdaytrade_detalhe numa só
    transação (reprocessar a mesma nota não duplica)."""
    try:
        session.exec(delete(operacoesDaytradeDetalhe)
                     .where(operacoesDaytradeDetalhe.notaDaytradeId == nota.id))
        for op in operacoes:
            session.add(operacoesDaytradeDetalhe(
                notaDaytradeId=nota.id,
                hashNota=hash_nota,
                corretora=nota.corretora,
                data=nota.data,
                nrNota=nota.nrNota,
                **op,
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise


def nota_registrada(session: Session, linha):
    """Acha em notasDaytrade a nota correspondente a uma linha extraída por
    extractDay.py, pela mesma chave única da tabela."""
    return session.exec(select(notasDaytrade).where(
        notasDaytrade.corretora == linha['corretora'],
        notasDaytrade.cpf == linha['cpf'],
        notasDaytrade.data == linha['data'],
        notasDaytrade.nCliente == linha['nCliente'],
    )).first()
