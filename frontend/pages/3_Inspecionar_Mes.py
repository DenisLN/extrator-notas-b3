import streamlit as st
import pandas as pd
import sys
from pathlib import Path
from sqlmodel import Session, select
from datetime import date
from collections import defaultdict
from decimal import Decimal

# Adiciona raiz ao path para importar schemas e módulos
sys.path.append(str(Path(__file__).parent.parent.parent))

from schemas import operacoesSwingtrade, eventosCorporativos, registroNomeAtivos, registroNotasSwing, notasDaytrade
from frontend.db_connection import get_session

def load_data():
    with get_session() as session:
        operacoes = session.exec(select(operacoesSwingtrade).order_by(operacoesSwingtrade.data, operacoesSwingtrade.id)).all()
        eventos = session.exec(select(eventosCorporativos).order_by(eventosCorporativos.data_com)).all()
        ativos_db = session.exec(select(registroNomeAtivos)).all()
        tipo_map = {a.nomeFantasia: a.tipoAtivo for a in ativos_db}
    return operacoes, eventos, tipo_map

def process_detailed_month(cpf, ano_mes, operacoes, eventos, tipo_map):
    # Filtra as operações e eventos do CPF/Gerais até o mês selecionado (inclusive)
    ops_cpf = [op for op in operacoes if op.cpf == cpf and op.data.strftime('%Y-%m') <= ano_mes]
    eventos_filtrados = [ev for ev in eventos if ev.data_com.strftime('%Y-%m') <= ano_mes]

    ops_por_data = defaultdict(list)
    for op in ops_cpf:
        ops_por_data[op.data].append(op)

    eventos_por_data = defaultdict(list)
    for ev in eventos_filtrados:
        eventos_por_data[ev.data_com].append(ev)

    todas_datas = sorted(set(list(ops_por_data.keys()) + list(eventos_por_data.keys())))
    
    portfolio = {}
    vendas_mes = []

    for data in todas_datas:
        data_str = data.isoformat()
        
        # Processar eventos
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
                pos['pm'] = max(Decimal('0'), pos['pm'] - Decimal(str(ev.valor_restituicao)))
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

        # Processar operações
        ops_dia = ops_por_data.get(data, [])
        ativos_dia = set(op.nomeAtivo for op in ops_dia)

        for ativo in ativos_dia:
            ops_ativo = [op for op in ops_dia if op.nomeAtivo == ativo]
            
            # Separar Day Trade
            dt_ops = [op for op in ops_ativo if op.isDayTrade]
            if dt_ops and data_str.startswith(ano_mes):
                # Calcular Day Trade do dia
                compras_dt = [op for op in dt_ops if op.operacaoTipo == 'C']
                vendas_dt = [op for op in dt_ops if op.operacaoTipo == 'V']
                if compras_dt and vendas_dt:
                    qtd_dt = sum(op.quantidade for op in compras_dt)
                    val_compra_dt = sum(op.precoOperacao + op.taxaRateada for op in compras_dt)
                    val_venda_dt = sum(op.precoOperacao - op.taxaRateada for op in vendas_dt)
                    
                    pm_compra_dt = val_compra_dt / qtd_dt
                    pm_venda_dt = val_venda_dt / qtd_dt
                    
                    lp_dt = val_venda_dt - val_compra_dt
                    vendas_mes.append({
                        'Data': data_str,
                        'Ativo': ativo,
                        'Tipo Ativo': tipo_map.get(ativo, 'ACAO'),
                        'Operação': 'Day Trade',
                        'Qtd': qtd_dt,
                        'PM Compra': float(pm_compra_dt),
                        'Líq Compra': float(val_compra_dt),
                        'PM Venda': float(pm_venda_dt),
                        'Líq Venda': float(val_venda_dt),
                        'Base (L/P)': float(lp_dt)
                    })
            
            # Separar Swing Trade
            st_ops = [op for op in ops_ativo if not op.isDayTrade]
            if not st_ops:
                continue
                
            compras_st = [op for op in st_ops if op.operacaoTipo == 'C']
            vendas_st = [op for op in st_ops if op.operacaoTipo == 'V']
            
            qtd_compras = sum(op.quantidade for op in compras_st)
            val_compras = sum(op.precoOperacao + op.taxaRateada for op in compras_st)
            
            qtd_vendas = sum(op.quantidade for op in vendas_st)
            val_vendas = sum(op.precoOperacao - op.taxaRateada for op in vendas_st)

            if ativo not in portfolio:
                portfolio[ativo] = {'qtd': 0, 'pm': Decimal('0'), 'custo_total': Decimal('0')}
            pos = portfolio[ativo]

            # Compras ST
            if qtd_compras > 0:
                pos['custo_total'] += val_compras
                pos['qtd'] += qtd_compras
                pos['pm'] = pos['custo_total'] / pos['qtd']

            # Vendas ST
            if qtd_vendas > 0:
                pm_antigo = pos['pm']
                pm_venda = val_vendas / qtd_vendas
                lp_venda = (pm_venda - pm_antigo) * qtd_vendas
                
                if data_str.startswith(ano_mes):
                    vendas_mes.append({
                        'Data': data_str,
                        'Ativo': ativo,
                        'Tipo Ativo': tipo_map.get(ativo, 'ACAO'),
                        'Operação': 'Swing Trade',
                        'Qtd': qtd_vendas,
                        'PM Compra': float(pm_antigo),
                        'Líq Compra': float(pm_antigo * qtd_vendas),
                        'PM Venda': float(pm_venda),
                        'Líq Venda': float(val_vendas),
                        'Base (L/P)': float(lp_venda)
                    })
                
                pos['custo_total'] -= pm_antigo * qtd_vendas
                pos['qtd'] -= qtd_vendas
                if pos['qtd'] <= 0:
                    pos['qtd'] = 0
                    pos['custo_total'] = Decimal('0')
                    pos['pm'] = Decimal('0')
                    
    return pd.DataFrame(vendas_mes)

def get_irrf_month(cpf, ano_mes):
    with get_session() as session:
        notas_swing = session.exec(select(registroNotasSwing).where(registroNotasSwing.cpf == cpf)).all()
        notas_day = session.exec(select(notasDaytrade).where(notasDaytrade.cpf == cpf)).all()
        
    irrf_list = []
    
    for nota in notas_swing:
        if nota.data.strftime('%Y-%m') == ano_mes:
            # IRRF exists in several fields.
            has_irrf = (nota.irrf and nota.irrf > 0) or (nota.irrfTotal and nota.irrfTotal > 0) or (nota.irrfAcao and nota.irrfAcao > 0) or (nota.irrfFii and nota.irrfFii > 0) or (nota.irrfBdr and nota.irrfBdr > 0) or (nota.irrfDay and nota.irrfDay > 0)
            if has_irrf:
                irrf_list.append({
                    'Data': nota.data.isoformat(),
                    'Corretora': nota.corretora,
                    'Nota': str(nota.nrNota),
                    'Tipo': 'Swing Trade',
                    'IRRF Ação': float(nota.irrfAcao or 0),
                    'IRRF FII': float(nota.irrfFii or 0),
                    'IRRF BDR': float(nota.irrfBdr or 0),
                    'IRRF Day': float(nota.irrfDay or 0),
                    'IRRF Total': float(nota.irrfTotal or nota.irrf or 0)
                })
                
    for nota in notas_day:
        if nota.data.strftime('%Y-%m') == ano_mes:
            if nota.irrf and nota.irrf > 0:
                irrf_list.append({
                    'Data': nota.data.isoformat(),
                    'Corretora': nota.corretora,
                    'Nota': '-',
                    'Tipo': 'Day Trade',
                    'IRRF Ação': 0.0,
                    'IRRF FII': 0.0,
                    'IRRF BDR': 0.0,
                    'IRRF Day': float(nota.irrf),
                    'IRRF Total': float(nota.irrf)
                })
                
    return pd.DataFrame(irrf_list)

def main():
    st.set_page_config(page_title="Inspecionar Mês", layout="wide")
    st.title("Inspecionar Mês")
    st.markdown("Veja os detalhes operação a operação do fechamento mensal para entender de onde vêm os valores.")
    
    try:
        operacoes, eventos, tipo_map = load_data()
    except Exception as e:
        st.error(f"Erro ao conectar ao banco de dados: {e}")
        return

    if not operacoes:
        st.warning("Nenhum registro encontrado.")
        return

    # Extrair lista de CPFs
    cpfs = list(set([op.cpf for op in operacoes]))
    selected_cpf = st.sidebar.selectbox("Selecione o CPF", options=cpfs)
    
    # Extrair lista de meses
    meses_set = set()
    for op in operacoes:
        if op.cpf == selected_cpf:
            meses_set.add(op.data.isoformat()[:7])
    
    meses_list = sorted(list(meses_set), reverse=True)
    if not meses_list:
        st.info("Nenhuma operação para este CPF.")
        return
        
    selected_mes = st.sidebar.selectbox("Selecione o Mês", options=meses_list)
    
    if st.sidebar.button("Analisar"):
        df_vendas = process_detailed_month(selected_cpf, selected_mes, operacoes, eventos, tipo_map)
        
        st.subheader(f"Movimentações Detalhadas - {selected_mes}")
        
        if df_vendas.empty:
            st.info("Nenhuma venda no período selecionado.")
        else:
            # Separar por tipo para melhor visualização
            for tipo in ['ACAO', 'FII', 'BDR']:
                df_tipo = df_vendas[df_vendas['Tipo Ativo'] == tipo]
                if not df_tipo.empty:
                    st.markdown(f"### {tipo}")
                    
                    df_tipo_disp = df_tipo.drop(columns=['Tipo Ativo'])
                    float_cols = ['PM Compra', 'Líq Compra', 'PM Venda', 'Líq Venda', 'Base (L/P)']
                    
                    st.dataframe(
                        df_tipo_disp.style.format({col: "{:.2f}" for col in float_cols})
                        .map(lambda x: "color: red" if isinstance(x, (int, float)) and x < 0 else "color: green" if isinstance(x, (int, float)) and x > 0 else "", subset=['Base (L/P)']),
                        width="stretch",
                        hide_index=True
                    )
                    
                    total_lp = df_tipo['Base (L/P)'].sum()
                    st.markdown(f"**Total L/P ({tipo}): R$ {total_lp:.2f}**")
                    st.divider()

        # Nova seção de IRRF
        st.subheader(f"Origem do IRRF - {selected_mes}")
        df_irrf = get_irrf_month(selected_cpf, selected_mes)
        
        if df_irrf.empty:
            st.info("Nenhum IRRF retido nas notas deste mês.")
        else:
            float_cols_irrf = ['IRRF Ação', 'IRRF FII', 'IRRF BDR', 'IRRF Day', 'IRRF Total']
            
            st.dataframe(
                df_irrf.style.format({col: "{:.2f}" for col in float_cols_irrf}),
                width="stretch",
                hide_index=True
            )
            
            total_irrf = df_irrf['IRRF Total'].sum()
            st.markdown(f"**Total IRRF Retido no Mês: R$ {total_irrf:.2f}**")
            st.divider()

if __name__ == "__main__":
    main()
