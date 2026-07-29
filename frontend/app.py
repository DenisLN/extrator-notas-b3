import streamlit as st

st.set_page_config(
    page_title="Dashboard Notas Corretagem",
    page_icon="📊",
    layout="wide"
)

st.title("Bem-vindo ao Dashboard de Operações")
st.markdown("""
### Escolha no menu lateral:
- **Day Trade**: Fechamento mensal com regras de IRRF e compensação de prejuízo
- **Swing Trade**: Acompanhamento de posição, eventos corporativos e preço médio (Em Breve)
""")
