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
    hashNota: str = Field(index=True, nullable=False)
    relativePath: str

class operacoesSwingtrade(SQLModel, table=True): 
    __table_args__ = (UniqueConstraint("corretora",
                                       "cpf",
                                       "data",
                                       "nCliente",
                                       "nomeAtivo",
                                       "operacaoTipo",
                                       "nrNota"),
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
    liquidoCalc: Decimal = Field(max_digits=10, decimal_places=2)
    liquidoReal: Decimal = Field(max_digits=10, decimal_places=2)
    ativosNegociados: Dict[str, Any] = Field(default_factory=dict,
                                             sa_column=Column(JSONB)
                                            )
    relativePath: str

class registroNomeAtivos(SQLModel, table=True): 
    __table_args__ = (UniqueConstraint("nomeAtivo",
                                       "nomeFantasia"),
                      )
    id: int | None = Field(default=None, primary_key=True)
    nomeAtivo: str = Field(index=True)
    nomeFantasia: str = Field(index=True)

class eventosCorporativos(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    data_com: date = Field(index=True)
    tipoEvento: str 
    ativoOriginal: str = Field(index=True)
    ativoNovo: str | None = Field(default=None) 
    fator: Decimal | None = Field(default=None, max_digits=10, decimal_places=4)
    valor_restituicao: Decimal | None = Field(default=None, max_digits=10, decimal_places=4)
    descricao: str | None = Field(default=None)
