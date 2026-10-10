"""Confere (e, com --gravar, preenche) o detalhe por operação das notas de
Day Trade já isoladas (D@*.pdf), sem passar pelo process_notas.py.

Serve para:
  - validar a extração de extractDayDetalhe.py em notas reais;
  - preencher operacoesdaytrade_detalhe para notas processadas antes de o
    detalhe existir, ou cujo detalhe falhou (idempotente: substitui as
    operações da nota).

Lembrete: essas operações NÃO são usadas em nenhum cálculo (ver NOTICE.md).

Uso:
  python scripts/daytrade_detalhe.py                   # só confere, todas as D@ do OUTPUT_DIR
  python scripts/daytrade_detalhe.py --gravar          # confere e grava no DATABASE_URL do .env
  python scripts/daytrade_detalhe.py CAMINHO [...]     # arquivos ou pastas específicos

A saída não mostra CPF, nome nem conta: só nº da nota, data e corretora.
Código de saída: 0 se todas as notas bateram (e gravaram, com --gravar); 1 se não.
"""
import argparse
import logging
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv
load_dotenv(RAIZ / ".env")

import pymupdf as pymu

import extractDayDetalhe as det
from areaCodes import areaDict_btg_day, areaDict_xp_day, brokerName_day
from cleanupFunctions import cleanup_dict

# Rótulos do resumo da nota usados para fechar a conta até o líquido que o
# extractDay.py já grava em notasDaytrade.liquido:
#   líquido = valor dos negócios - custos/despesas - impostos - IRRF day trade (proj.)
CUSTOS = {
    'BTG': ['Total das despesas'],
    'XP': ['Total de custos operacionais', 'Impostos'],
}


def corretora(page):
    for entry in brokerName_day:
        bloco = page.get_textbox(brokerName_day[entry])
        if "BTG" in bloco:
            return 'BTG', areaDict_btg_day
        if "XP" in bloco:
            return 'XP', areaDict_xp_day
    return None, None


def notas_do_arquivo(doc, coordmap):
    """Mesmo critério de extractDay.py: página com líquido ' |' continua na
    seguinte. Devolve listas de páginas (0-based), uma por nota."""
    notas, atual = [], []
    for i in range(len(doc)):
        atual.append(i)
        if doc[i].get_textbox(coordmap['liquido']) != " |":
            notas.append(atual)
            atual = []
    return notas


def assinado(par):
    valor, sinal = par
    return -valor if sinal == 'D' else valor


def conferir_liquido(page, broker, coordmap, conf):
    """Fecha a conta: soma das operações -> líquido lido por extractDay."""
    y = det.REGIOES[broker][3]
    liquido = cleanup_dict['liquido'](page.get_textbox(coordmap['liquido']))
    irrf_dt = det._valor_do_resumo(page, 'IRRF Day Trade (proj.)', y)
    custos = []
    for rotulo in CUSTOS[broker]:
        v = det._valor_do_resumo(page, rotulo, y)
        if v is None:
            return None, liquido
        custos.append(-abs(v[0]) if v[1] in (None, 'D') else v[0])
    esperado = conf['soma_operacoes'] + sum(custos) - (irrf_dt[0] if irrf_dt else Decimal('0'))
    return esperado, liquido


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caminhos", nargs="*", help="PDFs D@ ou pastas (padrão: OUTPUT_DIR do .env)")
    ap.add_argument("--gravar", action="store_true", help="grava em operacoesdaytrade_detalhe (DATABASE_URL do .env)")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostra cada nota")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)

    caminhos = [Path(c) for c in args.caminhos] or [Path(os.getenv("OUTPUT_DIR", "."))]
    arquivos = []
    for c in caminhos:
        arquivos += sorted(c.rglob("D@*.pdf")) if c.is_dir() else [c]

    session = None
    if args.gravar:
        from sqlmodel import SQLModel
        from db_connection import engine, get_session
        from schemas import operacoesDaytradeDetalhe
        SQLModel.metadata.create_all(engine, tables=[operacoesDaytradeDetalhe.__table__])
        session = get_session()

    st = defaultdict(Counter)
    tipos = Counter()
    marcadores = Counter()
    problemas = []
    for arq in arquivos:
        doc = pymu.open(arq)
        try:
            broker, coordmap = corretora(doc[0])
            if broker is None:
                problemas.append(f"{arq.name[:13]}...: corretora não reconhecida")
                st['?']['falha'] += 1
                continue
            with open(arq, "rb") as f:
                import xxhash
                hash_nota = xxhash.xxh64(f.read()).hexdigest()
            for paginas in notas_do_arquivo(doc, coordmap):
                p0 = doc[paginas[0]]
                nr = cleanup_dict['nrNota'](p0.get_textbox(coordmap['nrNota']))
                data = cleanup_dict['data'](p0.get_textbox(coordmap['data']))
                ident = f"{broker} nota nº {nr} de {data}"
                st[broker]['notas'] += 1
                try:
                    ops, conf = det.extrair_nota(doc, broker, paginas)
                except det.ErroDetalheDaytrade as e:
                    st[broker]['falha'] += 1
                    problemas.append(f"{ident}: {e}")
                    continue
                st[broker]['bateu_valor_negocios'] += 1
                st[broker]['operacoes'] += len(ops)
                for op in ops:
                    tipos[(broker, op['tipoNegocio'])] += 1
                    if op['marcadores']:
                        marcadores[(broker, op['marcadores'])] += 1
                esperado, liquido = conferir_liquido(doc[paginas[-1]], broker, coordmap, conf)
                if esperado is not None and esperado == liquido:
                    st[broker]['bateu_liquido'] += 1
                else:
                    problemas.append(f"{ident}: líquido da nota {liquido} x calculado das operações {esperado} "
                                     f"(o detalhe em si bateu com o valor dos negócios)")
                if args.verbose:
                    logging.info(f"{ident}: {len(ops)} operações, soma {conf['soma_operacoes']}, líquido {liquido}")
                if session is not None:
                    linha = {
                        'corretora': broker,
                        'cpf': cleanup_dict['cpf'](p0.get_textbox(coordmap['cpf'])),
                        'data': data,
                        'nCliente': cleanup_dict['nCliente'](p0.get_textbox(coordmap['nCliente'])),
                    }
                    nota = det.nota_registrada(session, linha)
                    if nota is None:
                        st[broker]['sem_nota_no_banco'] += 1
                        problemas.append(f"{ident}: nota não está em notasDaytrade; detalhe não gravado")
                        continue
                    det.gravar_detalhe(session, nota, ops, hash_nota)
                    st[broker]['gravadas'] += 1
        finally:
            doc.close()

    if session is not None:
        session.close()

    for p in problemas[:40]:
        logging.warning(p)
    if len(problemas) > 40:
        logging.warning(f"... e mais {len(problemas) - 40}")
    logging.info("Tipos de negócio: " + ", ".join(f"{b} {t!r}={n}" for (b, t), n in sorted(tipos.items())))
    if marcadores:
        logging.info("Marcadores: " + ", ".join(f"{b} {m!r}={n}" for (b, m), n in sorted(marcadores.items())))
    ok = True
    for b in sorted(st):
        s = st[b]
        logging.info(f"{b}: {s['notas']} notas, {s['operacoes']} operações; soma = valor dos negócios (e taxa operacional) "
                     f"em {s['bateu_valor_negocios']}; fecha até o líquido em {s['bateu_liquido']}; falhas {s['falha']}"
                     + (f"; gravadas {s['gravadas']}, sem nota no banco {s['sem_nota_no_banco']}" if args.gravar else ""))
        if s['falha'] or s['bateu_valor_negocios'] != s['notas'] or (args.gravar and s['sem_nota_no_banco']):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
