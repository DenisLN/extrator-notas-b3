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

class operacoesDaytradeDetalhe(SQLModel, table=True):
    """Uma linha por operação da tabela de negócios de uma nota de Day Trade
    (BM&F), ligada à nota em notasDaytrade. Extraída por extractDayDetalhe.py.

    ATENÇÃO: só registro. Nenhum cálculo usa esta tabela (ver NOTICE.md).
    Reprocessar a nota substitui as linhas dela (idempotente)."""
    __tablename__ = "operacoesdaytrade_detalhe"
    __table_args__ = (UniqueConstraint("notaDaytradeId",
                                       "ordem",
                                       name="detalhe_daytrade_ordem"),
                      )

    id: int | None = Field(default=None, primary_key=True)
    notaDaytradeId: int = Field(foreign_key="notasdaytrade.id", index=True)
    hashNota: str                             # xxhash64 do PDF isolado lido
    corretora: str
    data: date = Field(index=True)            # data do pregão (da nota)
    nrNota: int
    pagina: int                               # página do PDF isolado, a partir de 1
    ordem: int                                # posição da operação na nota, a partir de 1
    operacaoTipo: str                         # 'C' ou 'V' (coluna C/V)
    mercadoria: str                           # ex. 'WINV26' (espaços tirados: a XP escreve 'WIN V26')
    vencimento: date | None = Field(default=None)
    quantidade: int                           # pode ser negativa (AJUPOS)
    precoAjuste: Decimal = Field(max_digits=18, decimal_places=4)
    tipoNegocio: str                          # 'DAY TRADE'; também 'NORMAL', 'AJUPOS', 'TX. PERMANÊNCIA'
    valorOperacao: Decimal = Field(max_digits=14, decimal_places=2)  # sem sinal; o sinal está em debitoCredito
    debitoCredito: str | None = Field(default=None)  # 'D' ou 'C'; vazio quando o valor é 0,00
    taxaOperacional: Decimal = Field(max_digits=12, decimal_places=2)
    marcadores: str | None = Field(default=None)  # símbolos de rodapé colados aos campos, ex. '@' (XP: corretora/pessoa vinculada na contraparte)

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
