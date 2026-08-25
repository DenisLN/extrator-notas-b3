"""
computeSwing.py
Calcula e persiste os resultados de swing trade por CPF.
Chamado após cada nota swing processada com sucesso.
Nunca chamado diretamente pelo frontend.
"""

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from sqlmodel import Session, select, delete

from schemas import (
    operacoesSwingtrade,
    eventosCorporativos,
    registroNomeAtivos,
    resultadoMensalSwing,
    posicaoSwing,
)


def recalcular_swing(cpf: str, session: Session) -> None:
    """
    Recalcula do zero o estado de swing trade para um CPF.
    Apaga e recria os registros em resultadoMensalSwing e posicaoSwing.
    """
    logging.info(f"[computeSwing] Recalculando swing trade para CPF {cpf}...")

    # 1. Limpar resultados anteriores para esse CPF
    session.exec(delete(resultadoMensalSwing).where(resultadoMensalSwing.cpf == cpf))
    session.exec(delete(posicaoSwing).where(posicaoSwing.cpf == cpf))
    session.commit()

    # 2. Carregar APENAS operações swing (isDayTrade=False), ordenadas por data e id
    ops = session.exec(
        select(operacoesSwingtrade)
        .where(
            operacoesSwingtrade.cpf == cpf,
            operacoesSwingtrade.isDayTrade == False,
        )
        .order_by(operacoesSwingtrade.data, operacoesSwingtrade.id)
    ).all()

    if not ops:
        logging.info(f"[computeSwing] Nenhuma operação swing para CPF {cpf}.")
        return

    # 3. Carregar eventos corporativos (todos, sem filtro de CPF)
    eventos = session.exec(
        select(eventosCorporativos).order_by(eventosCorporativos.data_com)
    ).all()

    # 4. Construir mapa de tipoAtivo por nomeFantasia
    ativos_db = session.exec(select(registroNomeAtivos)).all()
    tipo_map: dict[str, str] = {a.nomeFantasia: a.tipoAtivo for a in ativos_db}

    # 5. Agrupar por data
    ops_por_data: dict[date, list] = defaultdict(list)
    for op in ops:
        ops_por_data[op.data].append(op)

    eventos_por_data: dict[date, list] = defaultdict(list)
    for ev in eventos:
        eventos_por_data[ev.data_com].append(ev)

    todas_datas = sorted(set(list(ops_por_data.keys()) + list(eventos_por_data.keys())))

    # 6. Percorrer datas e simular portfólio
    # portfolio: nomeAtivo -> {'qtd': int, 'pm': Decimal, 'custo_total': Decimal}
    portfolio: dict[str, dict] = {}

    # resultados_mensais: ano_mes -> tipoAtivo -> {'lp': Decimal, 'vol': Decimal}
    resultados_mensais: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {'lp': Decimal('0'), 'vol': Decimal('0')})
    )

    for data in todas_datas:
        ano_mes = data.strftime('%Y-%m')

        # 6a. Processar eventos corporativos do dia
        for ev in eventos_por_data.get(data, []):
            ativo = ev.ativoOriginal
            if ativo not in portfolio or portfolio[ativo]['qtd'] <= 0:
                continue
            pos = portfolio[ativo]

            if ev.tipoEvento in ('SPLIT', 'AGRUPAMENTO') and ev.fator:
                fator = Decimal(str(ev.fator))
                pos['qtd'] = int(pos['qtd'] * fator)
                pos['pm'] = pos['pm'] / fator if fator > 0 else Decimal('0')
                pos['custo_total'] = pos['pm'] * pos['qtd']

            elif ev.tipoEvento == 'RESTITUICAO_CAPITAL' and ev.valor_restituicao:
                pos['pm'] = max(Decimal('0'), pos['pm'] - ev.valor_restituicao)
                pos['custo_total'] = pos['pm'] * pos['qtd']

            elif ev.tipoEvento in ('MUDANCA_TICK', 'FUSAO') and ev.ativoNovo:
                novo = ev.ativoNovo
                fator_fusao = Decimal(str(ev.fator)) if ev.fator else Decimal('1')
                nova_qtd = int(pos['qtd'] * fator_fusao)
                novo_pm = pos['pm'] / fator_fusao if fator_fusao > 0 else Decimal('0')
                if novo not in portfolio:
                    portfolio[novo] = {'qtd': 0, 'pm': Decimal('0'), 'custo_total': Decimal('0')}
                portfolio[novo]['custo_total'] += nova_qtd * novo_pm
                portfolio[novo]['qtd'] += nova_qtd
                if portfolio[novo]['qtd'] > 0:
                    portfolio[novo]['pm'] = portfolio[novo]['custo_total'] / portfolio[novo]['qtd']
                pos['qtd'] = 0
                pos['pm'] = Decimal('0')
                pos['custo_total'] = Decimal('0')

        # 6b. Processar operações swing do dia
        ops_dia = ops_por_data.get(data, [])
        if not ops_dia:
            continue

        ativos_dia = set(op.nomeAtivo for op in ops_dia)

        for ativo in ativos_dia:
            ops_ativo = [op for op in ops_dia if op.nomeAtivo == ativo]
            compras = [op for op in ops_ativo if op.operacaoTipo == 'C']
            vendas  = [op for op in ops_ativo if op.operacaoTipo == 'V']

            qtd_compras = sum(op.quantidade for op in compras)
            qtd_vendas  = sum(op.quantidade for op in vendas)

            # Valor líquido de cada lado (já com taxa rateada embutida)
            val_compras = sum(op.precoOperacao + op.taxaRateada for op in compras)
            val_vendas  = sum(op.precoOperacao - op.taxaRateada for op in vendas)

            if ativo not in portfolio:
                portfolio[ativo] = {'qtd': 0, 'pm': Decimal('0'), 'custo_total': Decimal('0')}

            pos = portfolio[ativo]

            # Registrar compras no portfólio (atualiza PM ponderado)
            if qtd_compras > 0:
                pos['custo_total'] += val_compras
                pos['qtd'] += qtd_compras
                pos['pm'] = pos['custo_total'] / pos['qtd']

            # Registrar vendas: L/P = (preço médio de venda líquido - PM do portfólio) × qtd
            if qtd_vendas > 0:
                pm_venda = val_vendas / qtd_vendas
                lp = (pm_venda - pos['pm']) * qtd_vendas
                tipo = tipo_map.get(ativo, 'ACAO')

                resultados_mensais[ano_mes][tipo]['lp']  += lp
                resultados_mensais[ano_mes][tipo]['vol'] += val_vendas

                # Baixar posição ao PM atual (custo sai pelo PM, não pelo preço de venda)
                pos['custo_total'] -= pos['pm'] * qtd_vendas
                pos['qtd'] -= qtd_vendas
                if pos['qtd'] <= 0:
                    pos['qtd'] = 0
                    pos['custo_total'] = Decimal('0')
                    pos['pm'] = Decimal('0')

    # 7. Persistir resultados mensais
    for ano_mes, tipos in resultados_mensais.items():
        for tipo, dados in tipos.items():
            lp  = dados['lp'].quantize(Decimal('0.01'))
            vol = dados['vol'].quantize(Decimal('0.01'))
            isento = (tipo == 'ACAO' and vol <= Decimal('20000') and lp > 0)
            reg = resultadoMensalSwing(
                cpf=cpf,
                ano_mes=ano_mes,
                tipoAtivo=tipo,
                lucro_prejuizo=lp,
                volume_vendas=vol,
                isento=isento,
            )
            session.add(reg)

    # 8. Persistir posição atual (apenas ativos com quantidade > 0)
    for ativo, pos in portfolio.items():
        if pos['qtd'] <= 0:
            continue
        tipo = tipo_map.get(ativo, 'ACAO')
        reg = posicaoSwing(
            cpf=cpf,
            nomeAtivo=ativo,
            tipoAtivo=tipo,
            quantidade=int(pos['qtd']),
            precoMedio=pos['pm'].quantize(Decimal('0.0001')),
            custoTotal=pos['custo_total'].quantize(Decimal('0.01')),
            ultimaAtualizacao=date.today(),
        )
        session.add(reg)

    session.commit()
    logging.info(f"[computeSwing] Recálculo concluído para CPF {cpf}.")
