import streamlit as st
import pandas as pd
from decimal import Decimal
import sys
import importlib
from pathlib import Path

# Adiciona raiz ao path para importar schemas
sys.path.append(str(Path(__file__).parent.parent.parent))

# Reutilizar funções da aba de Swing Trade
swing_trade_mod = importlib.import_module("frontend.pages.2_Swing_Trade")
load_data = swing_trade_mod.load_data
get_asset_type = swing_trade_mod.get_asset_type

def process_day_trade(cpf, operacoes, notas):
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
    
    # Agrupar operações por data
    ops_por_data = {}
    for op in ops_cpf:
        data_str = op.data.isoformat()
        if data_str not in ops_por_data:
            ops_por_data[data_str] = []
        ops_por_data[data_str].append(op)
        
    todas_datas = sorted(list(ops_por_data.keys()))
    
    historico_day_trade = []
    
    for data_str in todas_datas:
        ano_mes = data_str[:7] # YYYY-MM
        
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
            
            if day_trade_qtd > 0:
                lucro_prejuizo_dt = (pm_venda_dia - pm_compra_dia) * day_trade_qtd
                volume_dt_venda = pm_venda_dia * day_trade_qtd
                
                historico_day_trade.append({
                    'Mes': ano_mes,
                    'Ativo': ativo,
                    'Tipo': get_asset_type(ativo),
                    'Lucro_Prejuizo': lucro_prejuizo_dt,
                    'Volume_Venda': volume_dt_venda
                })
                
    df_day_trade = pd.DataFrame(historico_day_trade) if historico_day_trade else pd.DataFrame(columns=['Mes', 'Ativo', 'Tipo', 'Lucro_Prejuizo', 'Volume_Venda'])
    
    # Extrair o IRRF Day retido nas notas por mês
    irrf_day_por_mes = {}
    for n in notas:
        if n.cpf == cpf:
            mes = n.data.isoformat()[:7]
            irrf_day_val = float(n.irrfDay) if n.irrfDay is not None else 0.0
            irrf_day_por_mes[mes] = irrf_day_por_mes.get(mes, 0.0) + irrf_day_val
            
    prejuizo_acumulado_dt = {'FII': 0.0, 'BDR': 0.0, 'AÇÕES': 0.0}
    darf_records_detalhe = []
    darf_records_consolidado = []
    
    meses_com_dados = set()
    if not df_day_trade.empty:
        meses_com_dados.update(df_day_trade['Mes'].unique())
    meses_com_dados.update(irrf_day_por_mes.keys())
    meses = sorted(list(meses_com_dados))
    
    for mes in meses:
        irrf_day_acumulado = irrf_day_por_mes.get(mes, 0.0)
        darf_bruto_mes_dt = 0.0
        vol_total_mes_dt = 0.0
        
        # 2. Processar Day Trade
        for tipo in ['FII', 'BDR', 'AÇÕES']:
            if not df_day_trade.empty:
                group = df_day_trade[(df_day_trade['Mes'] == mes) & (df_day_trade['Tipo'] == tipo)]
            else:
                group = pd.DataFrame()
                
            lp_total = group['Lucro_Prejuizo'].sum() if not group.empty else 0.0
            vol_total = group['Volume_Venda'].sum() if not group.empty else 0.0
            vol_total_mes_dt += vol_total
            
            if not group.empty:
                lucro_tributavel = 0.0
                darf_bruto = 0.0
                
                if lp_total < 0:
                    prejuizo_acumulado_dt[tipo] += abs(lp_total)
                else:
                    if lp_total > prejuizo_acumulado_dt[tipo]:
                        lucro_tributavel = lp_total - prejuizo_acumulado_dt[tipo]
                        prejuizo_acumulado_dt[tipo] = 0.0
                    else:
                        prejuizo_acumulado_dt[tipo] -= lp_total
                        lucro_tributavel = 0.0
                        
                    # Alíquota de Day Trade é 20% para todos
                    aliquota = 0.20
                    darf_bruto = lucro_tributavel * aliquota
                
                darf_records_detalhe.append({
                    'Mês': mes,
                    'Operação': 'Day Trade',
                    'Tipo': tipo,
                    'Volume Vendas (R$)': round(vol_total, 2),
                    'L/P do Mês (R$)': round(lp_total, 2),
                    'Isento?': 'Não (Day Trade)',
                    'Lucro Tributável (R$)': round(lucro_tributavel, 2),
                    'Prejuízo Acumulado (R$)': round(prejuizo_acumulado_dt[tipo], 2),
                    'DARF Bruto (R$)': round(darf_bruto, 2)
                })
                darf_bruto_mes_dt += darf_bruto
            
        irrf_dt = irrf_day_acumulado
            
        irrf_compensado_dt = min(darf_bruto_mes_dt, irrf_dt) if darf_bruto_mes_dt > 0 else 0.0
        darf_liquido_dt = darf_bruto_mes_dt - irrf_compensado_dt
        
        darf_liquido_total = darf_liquido_dt
        
        if darf_bruto_mes_dt > 0 or irrf_day_acumulado > 0:
            darf_records_consolidado.append({
                'Mês': mes,
                'Volume Vendas DT (R$)': round(vol_total_mes_dt, 2),
                'DARF Bruto DT (R$)': round(darf_bruto_mes_dt, 2),
                'IRRF Alocado DT (R$)': round(irrf_dt, 2),
                'DARF a Pagar (R$)': round(darf_liquido_total, 2)
            })
            
    df_darf_detalhe = pd.DataFrame(darf_records_detalhe)
    df_darf_consolidado = pd.DataFrame(darf_records_consolidado)

    return df_darf_detalhe, df_darf_consolidado

def main():
    st.set_page_config(page_title="Day Trade Bovespa", layout="wide")
    st.title("Dashboard Day Trade")
    
    try:
        operacoes, eventos, notas = load_data()
    except Exception as e:
        st.error(f"Erro ao conectar ao banco de dados: {e}")
        return

    if not operacoes:
        st.warning("Nenhum registro encontrado.")
        return

    cpfs = list(set([op.cpf for op in operacoes]))
    selected_cpf = st.sidebar.selectbox("Selecione o CPF", options=cpfs)

    df_darf_detalhe, df_darf_consolidado = process_day_trade(selected_cpf, operacoes, notas)

    st.subheader("L/P Detalhado (Day Trade)")
    if not df_darf_detalhe.empty:
        for tipo in ["AÇÕES", "FII", "BDR"]:
            df_tipo = df_darf_detalhe[df_darf_detalhe["Tipo"] == tipo]
            if not df_tipo.empty:
                st.markdown(f"#### {tipo}")
                df_tipo_disp = df_tipo.drop(columns=["Tipo"])
                float_cols = df_tipo_disp.select_dtypes(include=['float', 'float64']).columns
                st.dataframe(df_tipo_disp.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
    else:
        st.info("Nenhuma operação de Day Trade registrada para cálculo de DARF.")
        
    st.subheader("Resumo Consolidado DARF Mensal (Day Trade)")
    if not df_darf_consolidado.empty:
        float_cols = df_darf_consolidado.select_dtypes(include=['float', 'float64']).columns
        st.dataframe(df_darf_consolidado.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
    else:
        st.info("Nenhum DARF ou IRRF no período para Day Trade.")

if __name__ == "__main__":
    main()
