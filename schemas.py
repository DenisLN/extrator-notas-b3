from sqlmodel import Field, SQLModel, UniqueConstraint
from sqlalchemy import Column
from decimal import Decimal
from datetime import date
from typing import Any, Dict, Optional
from sqlalchemy.dialects.postgresql import JSONB

class notasDaytrade(SQLModel, table=True): 
    __table_args__ = (UniqueConstraint("corretora",
                                       "cpf",
                                       "data",
                                       "nCliente",
                                       name="operacao_exclusiva"),
                      )

    id: int | None = Field(default=None, primary_key=True)
    liquido: Decimal = Field(max_digits=10, decimal_places=2)
    semiLiquido:  Decimal = Field(max_digits=10, decimal_places=2)
    irrf: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    data: date = Field(index=True)
    cpf: str = Field(index=True)
    nCliente: str
    corretora: str
    nrNota: int
    hashNota: str = Field(index=True, nullable=False)
    relativePath: str

class operacoesSwingtrade(SQLModel, table=True): 
    __table_args__ = (UniqueConstraint("corretora",
                                       "cpf",
                                       "data",
                                       "nCliente",
                                       "nomeAtivo",
                                       "operacaoTipo",
                                       "nrNota",
                                       "isDayTrade"),
                      )
    id: int | None = Field(default=None, primary_key=True)
    data: date = Field(index=True)
    operacaoTipo: str
    nomeAtivo: str = Field(index=True)
    quantidade: int
    precoAjuste: Decimal = Field(max_digits=10, decimal_places=2)
    precoOperacao: Decimal = Field(max_digits=10, decimal_places=2)
    cpf: str = Field(index=True)
    nCliente: str
    corretora: str
    nrNota: int
    isDayTrade: bool = Field(default=False, index=True)
    taxaRateada: Decimal = Field(default=Decimal('0'), max_digits=10, decimal_places=4)

class registroNotasSwing(SQLModel, table=True): 
    __table_args__ = (UniqueConstraint("corretora",
                                       "cpf",
                                       "data",
                                       "nCliente",
                                       "nrNota"),)
    id: int | None = Field(default=None, primary_key=True)
    data: date = Field(index=True)
    nrNota: int
    corretora: str
    cpf: str = Field(index=True)
    nCliente: str = Field(index=True)
    hashNota: str = Field(index=True, nullable=False)
    taxas: Decimal = Field(max_digits=10, decimal_places=2)
    irrf: Decimal = Field(max_digits=10, decimal_places=2)
    irrfTotal: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    irrfAcao: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    irrfFii: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    irrfEtf: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    irrfBdr: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    irrfDay: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)
    liquidoCalc: Decimal = Field(max_digits=10, decimal_places=2)
    liquidoReal: Decimal = Field(max_digits=10, decimal_places=2)
    ativosNegociados: Dict[str, Any] = Field(default_factory=dict,
                                             sa_column=Column(JSONB)
                                            )
    relativePath: str

class resultadoMensalSwing(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("cpf", "ano_mes", "tipoAtivo"),)
    id: int | None = Field(default=None, primary_key=True)
    cpf: str = Field(index=True)
    ano_mes: str = Field(index=True)          # formato: "2026-03"
    tipoAtivo: str                            # "ACAO", "FII" ou "BDR"
    lucro_prejuizo: Decimal = Field(max_digits=10, decimal_places=2)
    volume_vendas: Decimal = Field(max_digits=10, decimal_places=2)
    isento: bool                              # True se ações < R$20.000/mês com lucro

class posicaoSwing(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("cpf", "nomeAtivo"),)
    id: int | None = Field(default=None, primary_key=True)
    cpf: str = Field(index=True)
    nomeAtivo: str = Field(index=True)
    tipoAtivo: str
    quantidade: int                           # INTEIRO, nunca Decimal
    precoMedio: Decimal = Field(max_digits=10, decimal_places=4)
    custoTotal: Decimal = Field(max_digits=10, decimal_places=2)
    ultimaAtualizacao: date

class registroNomeAtivos(SQLModel, table=True): 
    __table_args__ = (UniqueConstraint("nomeAtivo",
                                       "nomeFantasia"),
                      )
    id: int | None = Field(default=None, primary_key=True)
    nomeAtivo: str = Field(index=True)
    nomeFantasia: str = Field(index=True)
    tipoAtivo: str | None = Field(default=None, index=True)

class eventosCorporativos(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    data_com: date = Field(index=True)
    tipoEvento: str 
    ativoOriginal: str = Field(index=True)
    ativoNovo: str | None = Field(default=None) 
    fator: Decimal | None = Field(default=None, max_digits=10, decimal_places=4)
    valor_restituicao: Decimal | None = Field(default=None, max_digits=10, decimal_places=4)
    descricao: str | None = Field(default=None)
