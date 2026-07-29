import argparse
from datetime import datetime
from decimal import Decimal
from schemas import eventosCorporativos, SQLModel
from db_connection import engine, get_session

def init_db():
    SQLModel.metadata.create_all(engine)

def main():
    print("=== Registro de Eventos Corporativos ===")
    
    data_str = input("Data com do evento (YYYY-MM-DD): ").strip()
    try:
        data_com = datetime.strptime(data_str, "%Y-%m-%d").date()
    except ValueError:
        print("Erro: Data deve estar no formato YYYY-MM-DD.")
        return

    tipo_str = input("Tipo do evento (SPLIT, AGRUPAMENTO, FUSAO, RESTITUICAO_CAPITAL, MUDANCA_TICK): ").strip().upper()
    if tipo_str not in ['SPLIT', 'AGRUPAMENTO', 'FUSAO', 'RESTITUICAO_CAPITAL', 'MUDANCA_TICK']:
        print("Erro: Tipo de evento inválido.")
        return

    ativo = input("Ticker original (ex: PETR4): ").strip().upper()

    novo_ativo = None
    if tipo_str in ['MUDANCA_TICK', 'FUSAO']:
        novo_ativo = input("Novo ticker: ").strip().upper()

    fator = None
    if tipo_str in ['SPLIT', 'AGRUPAMENTO', 'FUSAO']:
        f_in = input("Fator multiplicador (ex: 10 para split 1->10, 0.1 para grupamento 10->1): ").strip()
        if f_in:
            fator = float(f_in)

    restituicao = None
    if tipo_str == 'RESTITUICAO_CAPITAL':
        r_in = input("Valor em reais restituído por ação: ").strip()
        if r_in:
            restituicao = float(r_in)

    descricao = input("Descrição ou notas sobre o evento (opcional): ").strip()

    evento = eventosCorporativos(
        data_com=data_com,
        tipoEvento=tipo_str,
        ativoOriginal=ativo,
        ativoNovo=novo_ativo if novo_ativo else None,
        fator=Decimal(str(fator)) if fator is not None else None,
        valor_restituicao=Decimal(str(restituicao)) if restituicao is not None else None,
        descricao=descricao if descricao else None
    )

    init_db() # Garante que a tabela existe
    
    with get_session() as session:
        session.add(evento)
        session.commit()
        print(f"\n✅ Evento corporativo {tipo_str} para {ativo} registrado com sucesso!")

if __name__ == "__main__":
    main()
