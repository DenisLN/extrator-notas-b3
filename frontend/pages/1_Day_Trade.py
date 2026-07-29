import streamlit as st
import pandas as pd
from sqlmodel import Session, select
from sqlalchemy import func
import sys
from pathlib import Path
import calendar
from datetime import date

sys.path.append(str(Path(__file__).parent.parent))
from schemas import notasDaytrade
from frontend.db_connection import get_session

def load_data_daily():
    with get_session() as session:
        soma_liquido = func.sum(notasDaytrade.liquido).label("soma_liquido")
        soma_semi_liquido = func.sum(notasDaytrade.semiLiquido).label("soma_semi_liquido")
        soma_irrf = func.sum(notasDaytrade.irrf).label("soma_irrf")

        statement = (
            select(
                notasDaytrade.cpf,
                notasDaytrade.corretora,
                notasDaytrade.data,
                soma_liquido,
                soma_semi_liquido,
                soma_irrf,
            )
            .group_by(notasDaytrade.cpf, notasDaytrade.corretora, notasDaytrade.data)
            .order_by(notasDaytrade.data)
        )
        results = session.exec(statement).all()
        
        data = []
        for row in results:
            data.append({
                "cpf": row.cpf,
                "corretora": row.corretora,
                "data": row.data,
                "mes": row.data.replace(day=1),
                "soma_liquido": float(row.soma_liquido or 0),
                "soma_semi_liquido": float(row.soma_semi_liquido or 0),
                "soma_irrf": float(row.soma_irrf or 0),
            })
        return pd.DataFrame(data)

def calc_resultado_liquido(row):
    if row['corretora'].upper() == "BTG":
        return row['soma_semi_liquido']
    else:
        return row['soma_liquido'] + row['soma_irrf']

def process_fechamento_mensal(df_filtered):
    if df_filtered.empty:
        return pd.DataFrame()

    df_filtered = df_filtered.copy()
    df_filtered['resultado_liquido'] = df_filtered.apply(calc_resultado_liquido, axis=1)

    records = []
    for (mes, corretora), group in df_filtered.groupby(["mes", "corretora"]):
        s_liq = group["soma_liquido"].sum()
        s_semi = group["soma_semi_liquido"].sum()
        s_irrf = group["soma_irrf"].sum()
        res_liq = group["resultado_liquido"].sum()

        records.append({
            "mes": mes,
            "corretora": corretora,
            "soma_liquido": s_liq,
            "soma_semi_liquido": s_semi,
            "soma_irrf": s_irrf,
            "resultado_liquido": res_liq,
        })

    df_grouped = pd.DataFrame(records).sort_values("mes")

    monthly_rows = []
    for mes, group in df_grouped.groupby("mes", sort=True):
        monthly_rows.append({
            "mes": mes,
            "corretoras": ", ".join(group["corretora"].unique()),
            "soma_irrf": group["soma_irrf"].sum(),
            "resultado_liquido": group["resultado_liquido"].sum(),
        })

    df_monthly = pd.DataFrame(monthly_rows)

    prejuizo_acumulado = 0.0
    irrf_acumulado = 0.0
    fechamento = []

    for idx, row in df_monthly.iterrows():
        mes = row["mes"]
        res_liq = row["resultado_liquido"]
        irrf_mes = row["soma_irrf"]
        
        irrf_acumulado += irrf_mes

        prejuizo_inicio = prejuizo_acumulado
        irrf_inicio = irrf_acumulado

        lucro_tributavel = 0.0
        imposto_devido = 0.0
        irrf_utilizado = 0.0
        darf_pagar = 0.0

        if res_liq < 0:
            prejuizo_acumulado += abs(res_liq)
        else:
            if prejuizo_acumulado > 0:
                if res_liq >= prejuizo_acumulado:
                    lucro_tributavel = res_liq - prejuizo_acumulado
                    prejuizo_acumulado = 0.0
                else:
                    prejuizo_acumulado -= res_liq
                    lucro_tributavel = 0.0
            else:
                lucro_tributavel = res_liq

            if lucro_tributavel > 0:
                imposto_devido = lucro_tributavel * 0.20
                if irrf_acumulado >= imposto_devido:
                    irrf_utilizado = imposto_devido
                    irrf_acumulado -= imposto_devido
                    darf_pagar = 0.0
                else:
                    irrf_utilizado = irrf_acumulado
                    darf_pagar = imposto_devido - irrf_acumulado
                    irrf_acumulado = 0.0

        fechamento.append({
            "Mês": str(mes),
            "Corretoras": row["corretoras"],
            "Resultado Líquido (R$)": round(res_liq, 2),
            "IRRF Mês (R$)": round(irrf_mes, 2),
            "Prejuízo Inicial (R$)": round(prejuizo_inicio, 2),
            "Lucro Tributável (R$)": round(lucro_tributavel, 2),
            "Imposto Devido 20% (R$)": round(imposto_devido, 2),
            "IRRF Compensado (R$)": round(irrf_utilizado, 2),
            "DARF a Pagar (R$)": round(darf_pagar, 2),
            "Prejuízo Acum. Fim (R$)": round(prejuizo_acumulado, 2),
            "IRRF Acum. Fim (R$)": round(irrf_acumulado, 2),
        })

    return pd.DataFrame(fechamento)

def render_calendar(df_filtered):
    st.subheader("📅 Calendário de Operações Day Trade")
    
    if df_filtered.empty:
        st.info("Nenhuma operação para mostrar no calendário.")
        return
        
    meses_disponiveis = df_filtered["mes"].unique()
    meses_str = [m.strftime("%Y-%m") for m in meses_disponiveis]
    meses_str.sort(reverse=True)
    
    selected_mes_str = st.selectbox("Selecione o Mês para o Calendário", options=meses_str)
    
    ano, mes = map(int, selected_mes_str.split("-"))
    df_mes = df_filtered[df_filtered["mes"] == date(ano, mes, 1)].copy()
    
    # Calculate daily results for the calendar
    df_mes['resultado_liquido'] = df_mes.apply(calc_resultado_liquido, axis=1)
    df_diario = df_mes.groupby('data').agg({'resultado_liquido': 'sum'}).reset_index()
    
    dias_operados = {}
    for _, row in df_diario.iterrows():
        dias_operados[row['data'].day] = row['resultado_liquido']
        
    cal = calendar.monthcalendar(ano, mes)
    dias_semana = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    
    st.markdown("### " + selected_mes_str)
    cols = st.columns(7)
    for i, col in enumerate(cols):
        col.markdown(f"**{dias_semana[i]}**")
        
    for semana in cal:
        cols = st.columns(7)
        for i, dia in enumerate(semana):
            if dia == 0:
                cols[i].write("")
            else:
                if dia in dias_operados:
                    res = dias_operados[dia]
                    emoji = "🟢" if res >= 0 else "🔴"
                    if cols[i].button(f"{dia} {emoji}", key=f"btn_{ano}_{mes}_{dia}"):
                        st.session_state["selected_date_dt"] = date(ano, mes, dia)
                else:
                    cols[i].write(f"{dia}")
                    
    # Display details for selected date
    if "selected_date_dt" in st.session_state:
        sel_date = st.session_state["selected_date_dt"]
        if sel_date.year == ano and sel_date.month == mes:
            st.markdown("---")
            st.markdown(f"### Detalhes do Dia: {sel_date.strftime('%d/%m/%Y')}")
            
            df_sel = df_mes[df_mes["data"] == sel_date]
            if not df_sel.empty:
                res_dia = df_sel['resultado_liquido'].sum()
                st.metric("Resultado Líquido do Dia", f"R$ {res_dia:,.2f}")
                df_sel_display = df_sel[["corretora", "soma_liquido", "soma_semi_liquido", "soma_irrf", "resultado_liquido"]]
                float_cols = df_sel_display.select_dtypes(include=['float', 'float64']).columns
                st.dataframe(df_sel_display.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
            else:
                st.info("Nenhuma operação neste dia.")

def main():
    st.set_page_config(page_title="Fechamento Day Trade", layout="wide")
    st.title("Fechamento Mês a Mês - Day Trade")

    try:
        df = load_data_daily()
    except Exception as e:
        st.error(f"Erro ao conectar ao banco de dados: {e}")
        return

    if df.empty:
        st.warning("Nenhum registro encontrado na tabela 'notasDaytrade'.")
        return

    st.sidebar.header("Filtros")
    cpfs = df["cpf"].unique().tolist()
    selected_cpf = st.sidebar.selectbox("Selecione o CPF", options=cpfs)

    brokers = ["TODAS"] + df[df["cpf"] == selected_cpf]["corretora"].unique().tolist()
    selected_broker = st.sidebar.selectbox("Selecione a Corretora", options=brokers)

    df_filtered = df[df["cpf"] == selected_cpf]
    if selected_broker != "TODAS":
        df_filtered = df_filtered[df_filtered["corretora"] == selected_broker]

    df_result = process_fechamento_mensal(df_filtered)

    if not df_result.empty:
        col1, col2, col3, col4 = st.columns(4)
        total_res = df_result["Resultado Líquido (R$)"].sum()
        total_darf = df_result["DARF a Pagar (R$)"].sum()
        prej_final = df_result["Prejuízo Acum. Fim (R$)"].iloc[-1]
        irrf_final = df_result["IRRF Acum. Fim (R$)"].iloc[-1]

        col1.metric("Resultado Acumulado", f"R$ {total_res:,.2f}")
        col2.metric("Total DARF a Pagar", f"R$ {total_darf:,.2f}")
        col3.metric("Prejuízo Acumulado Atual", f"R$ {prej_final:,.2f}")
        col4.metric("IRRF Acumulado a Compensar", f"R$ {irrf_final:,.2f}")

        st.subheader("Tabela de Fechamento Mensal")
        float_cols = df_result.select_dtypes(include=['float', 'float64']).columns
        st.dataframe(df_result.style.format({col: "{:.2f}" for col in float_cols}), width="stretch")
        
        st.markdown("---")
        render_calendar(df_filtered)

if __name__ == "__main__":
    main()
