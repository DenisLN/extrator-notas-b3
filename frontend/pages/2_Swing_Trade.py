import streamlit as st
import pandas as pd
from decimal import Decimal
import sys
from pathlib import Path
from sqlmodel import Session, select

sys.path.append(str(Path(__file__).parent.parent.parent))

from schemas import (
    operacoesSwingtrade,
    registroNotasSwing,
    resultadoMensalSwing,
    posicaoSwing,
)
from frontend.db_connection import get_session

def load_data(cpf: str):
    with get_session() as session:
        resultados = session.exec(
            select(resultadoMensalSwing)
            .where(resultadoMensalSwing.cpf == cpf)
            .order_by(resultadoMensalSwing.ano_mes)
        ).all()

        posicao = session.exec(
            select(posicaoSwing)
            .where(posicaoSwing.cpf == cpf)
        ).all()

        notas = session.exec(
            select(registroNotasSwing)
            .where(registroNotasSwing.cpf == cpf)
        ).all()

        # Buscar CPFs disponíveis (para o selectbox)
        cpfs_result = session.exec(
            select(operacoesSwingtrade.cpf).distinct()
        ).all()
        cpfs = [str(c) for c in cpfs_result]

    return resultados, posicao, notas, cpfs

def calc_darf(resultados, notas):
    """
    Cálculo de DARF mensal com prejuízo acumulado por tipo.
    É stateful (depende da ordem dos meses), por isso permanece no frontend.
    Complexidade: O(meses × 3 tipos) — sempre rápido.
    """
    from collections import defaultdict

    # Montar IRRF por mês/tipo a partir das notas
    irrf_por_mes: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {'ACAO': Decimal('0'), 'FII': Decimal('0'), 'BDR': Decimal('0')}
    )
    for n in notas:
        mes = n.data.strftime('%Y-%m')
        irrf_por_mes[mes]['ACAO'] += Decimal(str(n.irrfAcao)) if n.irrfAcao is not None else Decimal('0')
        irrf_por_mes[mes]['FII']  += Decimal(str(n.irrfFii)) if n.irrfFii is not None else Decimal('0')
        irrf_por_mes[mes]['BDR']  += Decimal(str(n.irrfBdr)) if n.irrfBdr is not None else Decimal('0')

    # Montar mapa de resultados mensais
    res_map: dict[tuple[str, str], resultadoMensalSwing] = {
        (r.ano_mes, r.tipoAtivo): r for r in resultados
    }

    todos_meses = sorted(set(
        list(irrf_por_mes.keys()) + [r.ano_mes for r in resultados]
    ))

    prej_acum = {'ACAO': Decimal('0'), 'FII': Decimal('0'), 'BDR': Decimal('0')}
    fechamento_detalhe = []
    fechamento_consolidado = []
    aliquota   = {'ACAO': Decimal('0.15'), 'FII': Decimal('0.20'), 'BDR': Decimal('0.15')}

    for mes in todos_meses:
        darf_bruto_mes = Decimal('0')
        darf_liq_mes = Decimal('0')
        irrf_retido_mes = {'ACAO': Decimal('0'), 'FII': Decimal('0'), 'BDR': Decimal('0')}
        vol_vendas_mes = Decimal('0')
        tem_movimento = False
        
        for tipo in ['ACAO', 'FII', 'BDR']:
            res = res_map.get((mes, tipo))
            lp      = Decimal(str(res.lucro_prejuizo)) if res else Decimal('0')
            vol     = Decimal(str(res.volume_vendas))  if res else Decimal('0')
            isento  = res.isento         if res else False
            irrf    = irrf_por_mes[mes][tipo]

            darf_bruto  = Decimal('0')
            luc_trib    = Decimal('0')

            if lp < 0:
                prej_acum[tipo] += abs(lp)
            elif not isento and lp > 0:
                if lp > prej_acum[tipo]:
                    luc_trib = lp - prej_acum[tipo]
                    prej_acum[tipo] = Decimal('0')
                else:
                    prej_acum[tipo] -= lp
                darf_bruto = luc_trib * aliquota[tipo]

            irrf_comp = min(darf_bruto, irrf) if darf_bruto > 0 else Decimal('0')
            darf_liq  = darf_bruto - irrf_comp

            darf_bruto_mes += darf_bruto
            darf_liq_mes += darf_liq
            irrf_retido_mes[tipo] = irrf
            vol_vendas_mes += vol

            if lp != 0 or irrf > 0 or darf_bruto > 0:
                tem_movimento = True
                fechamento_detalhe.append({
                    'Mês':                   mes,
                    'Tipo':                  tipo,
                    'Volume Vendas (R$)':    float(vol),
                    'L/P (R$)':              float(lp),
                    'Isento?':               'Sim' if isento else 'Não',
                    'Prej. Acum. (R$)':      float(prej_acum[tipo]),
                    'Lucro Tributável (R$)': float(luc_trib),
                    'Alíquota':              f"{float(aliquota[tipo]*100):.0f}%",
                    'DARF Bruto (R$)':       float(darf_bruto),
                    'IRRF Retido (R$)':      float(irrf),
                    'DARF a Pagar (R$)':     float(darf_liq),
                })

        if tem_movimento:
            fechamento_consolidado.append({
                'Mês': mes,
                'Vendas Totais (R$)': float(vol_vendas_mes),
                'DARF Bruto (R$)': float(darf_bruto_mes),
                'IRRF Retido Ações (R$)': float(irrf_retido_mes['ACAO']),
                'IRRF Retido FII (R$)': float(irrf_retido_mes['FII']),
                'IRRF Retido BDR (R$)': float(irrf_retido_mes['BDR']),
                'DARF Líquido a Pagar (R$)': float(darf_liq_mes),
            })

    return pd.DataFrame(fechamento_detalhe), pd.DataFrame(fechamento_consolidado)

def main():
    st.set_page_config(page_title="Swing Trade", layout="wide")
    st.title("Dashboard Swing Trade")

    with get_session() as session:
        cpfs_result = session.exec(select(operacoesSwingtrade.cpf).distinct()).all()
        cpfs = [str(c) for c in cpfs_result]

    if not cpfs:
        st.warning("Nenhum registro encontrado.")
        return

    selected_cpf = st.sidebar.selectbox("Selecione o CPF", options=cpfs)

    resultados, posicao, notas, _ = load_data(selected_cpf)

    st.subheader("Posição Atual")
    if posicao:
        df_posicao = pd.DataFrame([{
            'Ativo': p.nomeAtivo,
            'Tipo': p.tipoAtivo,
            'Quantidade': p.quantidade,
            'Preço Médio': float(p.precoMedio),
            'Custo Total': float(p.custoTotal),
            'Última Atualização': p.ultimaAtualizacao
        } for p in posicao])
        float_cols = df_posicao.select_dtypes(include=['float', 'float64']).columns
        st.dataframe(df_posicao.style.format({col: "{:.2f}" for col in float_cols}), width="stretch", hide_index=True)
    else:
        st.info("Nenhuma posição em aberto no momento.")

    df_detalhe, df_consolidado = calc_darf(resultados, notas)
    
    if not df_detalhe.empty:
        st.subheader("L/P Detalhado por Categoria")
        tabs = st.tabs(["Ações", "FIIs", "BDRs"])
        
        with tabs[0]:
            df_acao = df_detalhe[df_detalhe['Tipo'] == 'ACAO'].drop(columns=['Tipo'])
            if not df_acao.empty:
                float_cols = df_acao.select_dtypes(include=['float', 'float64']).columns
                st.dataframe(df_acao.style.format({col: "{:.2f}" for col in float_cols}), width="stretch", hide_index=True)
            else:
                st.info("Nenhuma movimentação em Ações no período.")
                
        with tabs[1]:
            df_fii = df_detalhe[df_detalhe['Tipo'] == 'FII'].drop(columns=['Tipo'])
            if not df_fii.empty:
                float_cols = df_fii.select_dtypes(include=['float', 'float64']).columns
                st.dataframe(df_fii.style.format({col: "{:.2f}" for col in float_cols}), width="stretch", hide_index=True)
            else:
                st.info("Nenhuma movimentação em FIIs no período.")
                
        with tabs[2]:
            df_bdr = df_detalhe[df_detalhe['Tipo'] == 'BDR'].drop(columns=['Tipo'])
            if not df_bdr.empty:
                float_cols = df_bdr.select_dtypes(include=['float', 'float64']).columns
                st.dataframe(df_bdr.style.format({col: "{:.2f}" for col in float_cols}), width="stretch", hide_index=True)
            else:
                st.info("Nenhuma movimentação em BDRs no período.")
                
        st.subheader("DARF Mensal Consolidado")
        float_cols = df_consolidado.select_dtypes(include=['float', 'float64']).columns
        st.dataframe(df_consolidado.style.format({col: "{:.2f}" for col in float_cols}), width="stretch", hide_index=True)
        
    else:
        st.info("Nenhum DARF ou resultado no período.")

if __name__ == "__main__":
    main()
