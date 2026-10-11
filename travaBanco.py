"""
travaBanco.py
Região crítica das gravações no banco.

process_notas.py processa vários PDFs ao mesmo tempo, um subprocesso por
PDF. A leitura dos PDFs roda em paralelo; a gravação não. Toda gravação (e a
leitura que decide o que gravar: "a nota já existe?", "o ativo já tem
ticker?", o recálculo do swing de um CPF) acontece dentro de
regiao_critica(), que segura uma trava consultiva (advisory lock) do
Postgres, uma só para o projeto inteiro.

Por que uma trava do Postgres, e não multiprocessing.Lock ou um arquivo:
- vale entre processos quaisquer, não só os filhos de um mesmo pai: o
  coletor no servidor e uma execução manual no Windows (pelo túnel) gravam
  no mesmo banco e se excluem do mesmo jeito;
- é solta pelo próprio Postgres quando a conexão cai: um subprocesso morto
  pelo timeout, ou que quebrou, não deixa a trava presa;
- não precisa de repetição como o SERIALIZABLE (que aborta uma das
  transações em conflito) nem bloqueia leitores como o LOCK TABLE.

Gravar é rápido (milissegundos por nota); ler o PDF é o que custa. Uma
trava única troca um pouco de espera por simplicidade: nenhuma combinação de
notas, CPFs ou ativos consegue se intercalar.

As restrições únicas das tabelas continuam valendo como última barreira.
"""

import logging
import os
import threading
import time
import zlib
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Chave da trava: fixa e própria deste projeto. A trava vale dentro de um
# banco (a base de teste não disputa com a real), mas o espaço de chaves do
# banco é compartilhado com qualquer outro programa que use advisory locks.
CHAVE_TRAVA = zlib.crc32(b"notascorretagem:gravacao")

# Quanto esperar pela trava antes de desistir com erro (segundos). Fica abaixo
# do timeout por arquivo de process_notas.py (NOTAS_TIMEOUT_POR_ARQUIVO).
ESPERA_MAXIMA = int(os.getenv("NOTAS_ESPERA_TRAVA_BANCO", "900"))

_estado = threading.local()


class TravaBancoIndisponivel(RuntimeError):
    """A trava do banco não veio dentro de ESPERA_MAXIMA."""


@contextmanager
def regiao_critica(engine=None):
    """Executa o bloco com a trava de gravação do banco.

    Reentrante na mesma thread: um bloco dentro de outro não espera por si
    mesmo. A trava fica numa conexão própria, aberta só para isso; as
    sessões do bloco usam outras conexões do pool."""
    if getattr(_estado, "profundidade", 0):
        _estado.profundidade += 1
        try:
            yield
        finally:
            _estado.profundidade -= 1
        return

    if engine is None:
        from db_connection import engine

    conn = engine.connect()
    try:
        # Deixa uma conexão pronta no pool para as sessões do bloco. Abrir
        # conexão é lento (pelo túnel SSH, ~2 s) e não pode acontecer com a
        # trava na mão: seria uma espera a mais para cada processo na fila.
        engine.connect().close()

        inicio = time.monotonic()
        try:
            # lock_timeout também vale para pg_advisory_lock.
            conn.execute(text(f"SET lock_timeout = '{ESPERA_MAXIMA}s'"))
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CHAVE_TRAVA})
            conn.commit()
        except DBAPIError as e:
            if getattr(e.orig, "pgcode", None) == "55P03":   # lock_not_available
                raise TravaBancoIndisponivel(
                    f"a trava de gravação do banco não foi liberada em {ESPERA_MAXIMA} s "
                    f"(outra gravação presa? veja pg_locks, objid {CHAVE_TRAVA})") from None
            raise
        obtida = time.monotonic()
        _estado.profundidade = 1
        try:
            yield
        finally:
            _estado.profundidade = 0
            logging.debug(f"Trava de gravação do banco: {obtida - inicio:.2f} s de espera, "
                          f"{time.monotonic() - obtida:.2f} s segurada.")
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CHAVE_TRAVA})
                conn.execute(text("RESET lock_timeout"))
                conn.commit()
            except Exception as e:
                # A conexão é descartada abaixo; o Postgres solta a trava ao fechá-la.
                logging.warning(f"Não consegui soltar a trava do banco de forma limpa ({e}); "
                                f"descartando a conexão.")
                conn.invalidate()
    finally:
        conn.close()
