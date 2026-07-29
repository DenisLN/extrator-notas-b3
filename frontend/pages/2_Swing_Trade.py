import streamlit as st
import pandas as pd
from decimal import Decimal
from sqlmodel import Session, select
from sqlalchemy import func
import sys
import re
from pathlib import Path

# Adiciona raiz ao path para importar schemas
sys.path.append(str(Path(__file__).parent.parent.parent))
from schemas import operacoesSwingtrade, eventosCorporativos, registroNotasSwing
from frontend.db_connection import get_session

def load_data():
    with get_session() as session:
        operacoes = session.exec(select(operacoesSwingtrade).order_by(operacoesSwingtrade.data)).all()
        eventos = session.exec(select(eventosCorporativos).order_by(eventosCorporativos.data_com)).all()
        notas = session.exec(select(registroNotasSwing)).all()
        return operacoes, eventos, notas

def get_asset_type(ativo):
    if ativo.endswith('11'):
        return 'FII'
    elif re.search(r'3[2-59]$', ativo):
        return 'BDR'
    else:
        return 'AÇÕES'

def process_swing_trade(cpf, operacoes, eventos, notas):
    # Filtrar por CPF
    ops_cpf = [op for op in operacoes if op.cpf == cpf]
    notas_cpf = { (n.corretora, n.data.isoformat(), n.nCliente, n.nrNota): float(n.taxas) for n in notas if n.cpf == cpf }
    
    # Ratear taxas proporcionalmente pelo volume financeiro de cada operação na respectiva nota
    volume_por_nota = {}
    taxas_rateadas = {}
    for op in ops_cpf:
        chave = (op.corretora, op.data.isoformat(), op.nCliente, op.nrNota)
        volume_por_nota[chave] = volume_por_nota.get(chave, 0.0) + float(op.precoOperacao)
        
    for op in ops_cpf:
        chave = (op.corretora, op.data.isoformat(), op.nCliente, op.nrNota)
        taxa_total = notas_cpf.get(chave, 0.0)
        vol_total = volume_por_nota.get(chave, 0.0)
        if vol_total > 0:
            taxas_rateadas[op.id] = (float(op.precoOperacao) / vol_total) * taxa_total
        else:
            taxas_rateadas[op.id] = 0.0
    
    # Agrupar operações por data para resolver Day Trades e processar Swing
    ops_por_data = {}
    for op in ops_cpf:
        data_str = op.data.isoformat()
        if data_str not in ops_por_data:
            ops_por_data[data_str] = []
        ops_por_data[data_str].append(op)
        
    eventos_por_data = {}
    for ev in eventos:
        data_str = ev.data_com.isoformat()
        if data_str not in eventos_por_data:
            eventos_por_data[data_str] = []
        eventos_por_data[data_str].append(ev)
        
    todas_datas = sorted(list(set(list(ops_por_data.keys()) + list(eventos_por_data.keys()))))
    
    portfolio = {} # ticker -> {'qtd': 0, 'pm': 0.0, 'custo_total': 0.0}
    historico_mensal = []
    historico_vendas = []
    
    for data_str in todas_datas:
        ano_mes = data_str[:7] # YYYY-MM
        
        # 1. Processar Eventos Corporativos do Dia
        if data_str in eventos_por_data:
            for ev in eventos_por_data[data_str]:
                ativo = ev.ativoOriginal
                if ativo in portfolio and portfolio[ativo]['qtd'] > 0:
                    pos = portfolio[ativo]
                    if ev.tipoEvento in ['SPLIT', 'AGRUPAMENTO'] and ev.fator:
                        fator = float(ev.fator)
                        pos['qtd'] = pos['qtd'] * fator
                        pos['pm'] = pos['pm'] / fator if fator > 0 else 0
                        pos['custo_total'] = pos['qtd'] * pos['pm']
                    
                    elif ev.tipoEvento == 'RESTITUICAO_CAPITAL' and ev.valor_restituicao:
                        val_rest = float(ev.valor_restituicao)
                        pos['pm'] = max(0, pos['pm'] - val_rest)
                        pos['custo_total'] = pos['qtd'] * pos['pm']
                        
                    elif ev.tipoEvento in ['MUDANCA_TICK', 'FUSAO'] and ev.ativoNovo:
                        novo_ativo = ev.ativoNovo
                        if novo_ativo not in portfolio:
                            portfolio[novo_ativo] = {'qtd': 0, 'pm': 0.0, 'custo_total': 0.0}
                        
                        fator_fusao = float(ev.fator) if ev.fator else 1.0
                        nova_qtd = pos['qtd'] * fator_fusao
                        novo_pm = pos['pm'] / fator_fusao if fator_fusao > 0 else 0
                        
                        portfolio[novo_ativo]['qtd'] += nova_qtd
                        portfolio[novo_ativo]['custo_total'] += (nova_qtd * novo_pm)
                        portfolio[novo_ativo]['pm'] = portfolio[novo_ativo]['custo_total'] / portfolio[novo_ativo]['qtd']
                        
                        pos['qtd'] = 0
                        pos['pm'] = 0.0
                        pos['custo_total'] = 0.0

        # 2. Processar Operações do Dia (Resolvendo Day Trade)
        if data_str in ops_por_data:
            ops_dia = ops_por_data[data_str]
            ativos_dia = set([op.nomeAtivo for op in ops_dia])
            
            for ativo in ativos_dia:
                ops_ativo = [op for op in ops_dia if op.nomeAtivo == ativo]
                
                compras = [op for op in ops_ativo if op.operacaoTipo == 'C']
                vendas = [op for op in ops_ativo if op.operacaoTipo == 'V']
                
                qtd_compras = sum([op.quantidade for op in compras])
                val_compras = sum([float(op.precoOperacao) + taxas_rateadas.get(op.id, 0.0) for op in compras])
                pm_compra_dia = val_compras / qtd_compras if qtd_compras > 0 else 0
                
                qtd_vendas = sum([op.quantidade for op in vendas])
                val_vendas = sum([float(op.precoOperacao) - taxas_rateadas.get(op.id, 0.0) for op in vendas])
                pm_venda_dia = val_vendas / qtd_vendas if qtd_vendas > 0 else 0
                
                day_trade_qtd = min(qtd_compras, qtd_vendas)
                swing_compra_qtd = qtd_compras - day_trade_qtd
                swing_venda_qtd = qtd_vendas - day_trade_qtd
                
                if ativo not in portfolio:
                    portfolio[ativo] = {'qtd': 0, 'pm': 0.0, 'custo_total': 0.0}
                
                if swing_compra_qtd > 0:
                    custo_nova_compra = swing_compra_qtd * pm_compra_dia
                    portfolio[ativo]['qtd'] += swing_compra_qtd
                    portfolio[ativo]['custo_total'] += custo_nova_compra
                    portfolio[ativo]['pm'] = portfolio[ativo]['custo_total'] / portfolio[ativo]['qtd']
                
                if swing_venda_qtd > 0:
                    lucro_prejuizo_venda = (pm_venda_dia - portfolio[ativo]['pm']) * swing_venda_qtd
                    volume_venda = pm_venda_dia * swing_venda_qtd
                    
                    historico_vendas.append({
                        'Mes': ano_mes,
                        'Ativo': ativo,
                        'Tipo': get_asset_type(ativo),
                        'Lucro_Prejuizo': lucro_prejuizo_venda,
                        'Volume_Venda': volume_venda
                    })
                    
                    portfolio[ativo]['qtd'] -= swing_venda_qtd
                    portfolio[ativo]['custo_total'] -= (swing_venda_qtd * portfolio[ativo]['pm'])
                    
                    if portfolio[ativo]['qtd'] <= 0:
                        portfolio[ativo]['qtd'] = 0
                        portfolio[ativo]['custo_total'] = 0.0
                        portfolio[ativo]['pm'] = 0.0
        
        # Snapshot mensal
        for ativo, pos in portfolio.items():
            if pos['qtd'] > 0:
                historico_mensal.append({
                    'Mes': ano_mes,
                    'Data': data_str,
                    'Ativo': ativo,
                    'Quantidade': pos['qtd'],
                    'Preco_Medio': round(pos['pm'], 4),
                    'Capital_Investido': round(pos['custo_total'], 2)
                })

    if not historico_mensal:
        df_mensal = pd.DataFrame()
    else:
        df_hist = pd.DataFrame(historico_mensal)
        df_hist = df_hist.sort_values(by=['Mes', 'Data'])
        df_mensal = df_hist.drop_duplicates(subset=['Mes', 'Ativo'], keep='last')
    
    carteira_atual = []
    for ativo, pos in portfolio.items():
        if pos['qtd'] > 0:
            carteira_atual.append({
                'Ativo': ativo,
                'Quantidade': pos['qtd'],
                'Preço Médio': round(pos['pm'], 2),
                'Total Investido': round(pos['custo_total'], 2)
            })
            
    # Processamento de L/P e DARF
    df_vendas = pd.DataFrame(historico_vendas) if historico_vendas else pd.DataFrame(columns=['Mes', 'Ativo', 'Tipo', 'Lucro_Prejuizo', 'Volume_Venda'])
    
    # Extrair o IRRF retido nas notas por mês
    irrf_por_mes = {}
    for n in notas:
        if n.cpf == cpf:
            mes = n.data.isoformat()[:7]
            irrf_por_mes[mes] = irrf_por_mes.get(mes, 0.0) + float(n.irrf)
            
    prejuizo_acumulado = {'FII': 0.0, 'BDR': 0.0, 'AÇÕES': 0.0}
    darf_records_detalhe = []
    darf_records_consolidado = []
    
    meses_com_dados = set()
    if not df_vendas.empty:
        meses_com_dados.update(df_vendas['Mes'].unique())
    meses_com_dados.update(irrf_por_mes.keys())
    meses = sorted(list(meses_com_dados))
    
    for mes in meses:
        # O IRRF "dedo-duro" do mês só pode ser compensado no próprio mês, não acumula para os próximos
        irrf_acumulado = irrf_por_mes.get(mes, 0.0)
        darf_bruto_mes = 0.0
        
        for tipo in ['FII', 'BDR', 'AÇÕES']:
            if not df_vendas.empty:
                group = df_vendas[(df_vendas['Mes'] == mes) & (df_vendas['Tipo'] == tipo)]
            else:
                group = pd.DataFrame()
                
            lp_total = group['Lucro_Prejuizo'].sum() if not group.empty else 0.0
            vol_total = group['Volume_Venda'].sum() if not group.empty else 0.0
            
            # Se não teve nenhuma movimentação de venda no mês para esse tipo e nem lucro/prejuízo, pula
            # A não ser que a gente queira mostrar o saldo de prejuízo arrastado, mas vamos seguir a lógica anterior
            if group.empty:
                continue
                
            # Check exemption for AÇÕES (só volume de AÇÕES conta para o limite de 20k)
            isento = False
            if tipo == 'AÇÕES' and vol_total <= 20000 and lp_total > 0:
                isento = True
                
            lucro_tributavel = 0.0
            darf_bruto = 0.0
            
            if lp_total < 0:
                prejuizo_acumulado[tipo] += abs(lp_total)
            else:
                if not isento:
                    if lp_total > prejuizo_acumulado[tipo]:
                        lucro_tributavel = lp_total - prejuizo_acumulado[tipo]
                        prejuizo_acumulado[tipo] = 0.0
                    else:
                        prejuizo_acumulado[tipo] -= lp_total
                        lucro_tributavel = 0.0
                        
                    aliquota = 0.20 if tipo == 'FII' else 0.15
                    darf_bruto = lucro_tributavel * aliquota
            
            # Calculamos apenas o DARF Bruto por tipo
            darf_records_detalhe.append({
                'Mês': mes,
                'Tipo': tipo,
                'Volume Vendas (R$)': round(vol_total, 2),
                'L/P do Mês (R$)': round(lp_total, 2),
                'Isento?': 'Sim' if isento else 'Não',
                'Lucro Tributável (R$)': round(lucro_tributavel, 2),
                'Prejuízo Acumulado (R$)': round(prejuizo_acumulado[tipo], 2),
                'DARF Bruto (R$)': round(darf_bruto, 2)
            })
            darf_bruto_mes += darf_bruto
            
        # Calcula o volume total de vendas no mês (ações + fii + bdr)
        vol_total_mes = 0.0
        if not df_vendas.empty:
            df_mes_vendas = df_vendas[df_vendas['Mes'] == mes]
            vol_total_mes = df_mes_vendas['Volume_Venda'].sum() if not df_mes_vendas.empty else 0.0
            
        # Compensação do IRRF retido no mês inteiro
        # Regra customizada: só compensa o IRRF se as vendas totais do mês > 20k
        irrf_compensado = 0.0
        if darf_bruto_mes > 0 and vol_total_mes > 20000:
            irrf_compensado = min(darf_bruto_mes, irrf_acumulado)
            darf_liquido_mes = darf_bruto_mes - irrf_compensado
        else:
            darf_liquido_mes = darf_bruto_mes
            
        # Adiciona o registro consolidado se houve vendas no mês ou IRRF retido
        if darf_bruto_mes > 0 or irrf_acumulado > 0:
            darf_records_consolidado.append({
                'Mês': mes,
                'Volume Total Vendas (R$)': round(vol_total_mes, 2),
                'Total DARF Bruto (R$)': round(darf_bruto_mes, 2),
                'IRRF do Mês (R$)': round(irrf_acumulado, 2),
                'IRRF Compensado (R$)': round(irrf_compensado, 2),
                'DARF a Pagar (R$)': round(darf_liquido_mes, 2)
            })
            
    df_darf_detalhe = pd.DataFrame(darf_records_detalhe)
    df_darf_consolidado = pd.DataFrame(darf_records_consolidado)

    return df_mensal, carteira_atual, df_darf_detalhe, df_darf_consolidado

def main():
    st.set_page_config(page_title="Swing Trade", layout="wide")
    st.title("Dashboard Swing Trade")
    
    try:
        operacoes, eventos, notas = load_data()
    except Exception as e:
        st.error(f"Erro ao conectar ao banco de dados: {e}")
        return

    if not operacoes:
        st.warning("Nenhum registro encontrado na tabela 'operacoesSwingtrade'.")
        return

    cpfs = list(set([op.cpf for op in operacoes]))
    selected_cpf = st.sidebar.selectbox("Selecione o CPF", options=cpfs)

    df_mensal, carteira_atual, df_darf_detalhe, df_darf_consolidado = process_swing_trade(selected_cpf, operacoes, eventos, notas)

    st.subheader("Carteira Atual (Posição em Aberto)")
    if carteira_atual:
        df_carteira = pd.DataFrame(carteira_atual)
        float_cols = df_carteira.select_dtypes(include=['float', 'float64']).columns
        st.dataframe(df_carteira.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
    else:
        st.info("Nenhuma posição em aberto no momento.")

    st.subheader("Evolução da Posição Mês a Mês")
    if not df_mensal.empty:
        ativos_disponiveis = sorted(df_mensal['Ativo'].unique())
        selected_ativo = st.selectbox("Selecione o Ativo para ver a evolução", options=["Todos"] + ativos_disponiveis)
        
        if selected_ativo == "Todos":
            pivot_df = df_mensal.pivot(index='Ativo', columns='Mes', values='Quantidade').fillna(0)
            float_cols = pivot_df.select_dtypes(include=['float', 'float64']).columns
            st.dataframe(pivot_df.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
        else:
            df_ativo_disp = df_ativo[['Mes', 'Quantidade', 'Preco_Medio', 'Capital_Investido']]
            float_cols = df_ativo_disp.select_dtypes(include=['float', 'float64']).columns
            st.dataframe(df_ativo_disp.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
    else:
        st.info("Sem dados suficientes para evolução mensal.")
        
    st.subheader("L/P Detalhado por Tipo de Ativo")
    if not df_darf_detalhe.empty:
        for tipo in ["AÇÕES", "FII", "BDR"]:
            df_tipo = df_darf_detalhe[df_darf_detalhe["Tipo"] == tipo]
            if not df_tipo.empty:
                st.markdown(f"#### {tipo}")
                df_tipo_disp = df_tipo.drop(columns=["Tipo"])
                float_cols = df_tipo_disp.select_dtypes(include=['float', 'float64']).columns
                st.dataframe(df_tipo_disp.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
    else:
        st.info("Nenhuma venda registrada para cálculo de DARF.")
        
    st.subheader("Resumo Consolidado DARF Mensal")
    if not df_darf_consolidado.empty:
        float_cols = df_darf_consolidado.select_dtypes(include=['float', 'float64']).columns
        st.dataframe(df_darf_consolidado.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
    else:
        st.info("Nenhum DARF ou IRRF no período.")

if __name__ == "__main__":
    main()
