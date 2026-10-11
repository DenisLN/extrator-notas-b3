import logging
import xxhash
import pymupdf as pymu
from pathlib import Path
from sqlmodel import Session
from sqlalchemy.exc import IntegrityError

from areaCodes import areaDict_btg_day, areaDict_xp_day, brokerName_day
from schemas import * 
from cleanupFunctions import cleanup_dict
from db_connection import engine, get_session
import extractDayDetalhe
from travaBanco import regiao_critica

SQLModel.metadata.create_all(engine)


def handle_files(directory, dry_run: bool = False):
    for pdf_path in directory.rglob("*.pdf"):
        logging.info(f"📁 Processando arquivo: {pdf_path}")
        try: 
            process_day_pdf(str(pdf_path), dry_run=dry_run)
        except Exception as e: 
            logging.exception(f"💥 Erro fatal ao processar {pdf_path}: {e}")
            exit()


def process_day_pdf(pdf_path: str, external_session: Session = None, dry_run: bool = False,
                    falhas_detalhe: list | None = None, resultado: dict | None = None) -> bool:
    """Lê os totais da nota de Day Trade para notasDaytrade e, à parte, as
    operações da nota para operacoesdaytrade_detalhe (extractDayDetalhe.py).

    O retorno (True/False) só diz respeito a notasDaytrade. Uma falha no
    detalhe por operação não muda o retorno: vai para o log como ERROR e,
    se falhas_detalhe for uma lista, é acrescentada a ela.

    A gravação acontece dentro da região crítica do banco (travaBanco.py);
    a leitura do PDF, fora. Se resultado for um dict, recebe 'ja_registrada'
    (True se todas as notas do arquivo já estavam em notasDaytrade)."""
    logging.debug(f"Iniciando leitura do PDF: {pdf_path}")
    with open(pdf_path, "rb") as f:
        file_hash = xxhash.xxh64(f.read()).hexdigest()
    logging.debug(f"Hash xxHash64 gerado: {file_hash}")

    doc = pymu.open(pdf_path)
    doc_len = len(doc)
    logging.debug(f"Total de páginas no documento: {doc_len}")
    
    start_page = doc[0]
    coordmap = {}
    broker = ''

    for entry in brokerName_day: 
        bloco = start_page.get_textbox(brokerName_day[entry])
        logging.debug(f"Checando corretora no bbox '{entry}': {repr(bloco)}")
        if "BTG" in bloco: 
            coordmap = areaDict_btg_day
            broker = 'BTG'
            break
        elif "XP" in bloco:
            coordmap = areaDict_xp_day  
            broker = 'XP'
            break

    if not coordmap: 
        logging.error(f"❌ Impossível Distinguir Corretora para o arquivo: {pdf_path}")
        return False
    logging.info(f"Corretora identificada: {broker}")

    i = 0
    linhas = [] 
    paginas_por_linha = []  # páginas (0-based) de cada nota, para o detalhe por operação
    inicio_nota = 0
    while i < doc_len: 
        page = doc[i]
        bloco_liquido = page.get_textbox(coordmap['liquido'])
        logging.debug(f"Página {i+1}/{doc_len} - Checagem 'liquido': {repr(bloco_liquido)}")

        if bloco_liquido == " |":
            logging.debug(f"Página {i+1} ignorada (indicador de continuação '|').")
            i += 1
        else: 
            linha = {}
            linha['corretora'] = broker
            linha['hashNota'] = file_hash
            
            rel_path = str(pdf_path).replace("z:\\", "").replace("Z:\\", "").replace("z:/", "").replace("Z:/", "").replace("/mnt/", "")
            rel_path = rel_path.replace("Projetos\\notascorretagem\\", "").replace("Projetos/notascorretagem/", "")
            # Normaliza separadores e garante o prefixo "D@" no nome do arquivo,
            # independente de process_day_pdf ter sido chamado a partir do
            # arquivo já isolado por process_notas.py (que monta o nome com
            # "D@...") ou diretamente sobre um PDF ainda sem esse prefixo.
            # Bug histórico: quando chamado fora do fluxo de process_notas.py,
            # o relativePath gravado ficava sem "D@", diferente do Swing Trade
            # (extractSwing.py), que sempre recebe o arquivo já isolado com
            # "S@". Isso deixou ~429 registros antigos no banco sem o prefixo
            # mesmo o arquivo real em disco já tendo sido renomeado com "D@".
            rel_path = rel_path.replace("\\", "/")
            rel_dir, _, rel_filename = rel_path.rpartition("/")
            if rel_filename and not rel_filename.startswith("D@"):
                rel_filename = f"D@{rel_filename}"
            rel_path = f"{rel_dir}/{rel_filename}" if rel_dir else rel_filename
            linha['relativePath'] = rel_path
            
            for entry in coordmap: 
                if entry == 'continua' or entry not in cleanup_dict:
                    continue
                raw_text = page.get_textbox(coordmap[entry])
                clean_text = cleanup_dict[entry](raw_text)
                logging.debug(f"Campo '{entry}': raw={repr(raw_text)} -> clean={repr(clean_text)}")
                linha[entry] = clean_text
                
            logging.debug(f"Dados brutos extraídos da página {i+1}: {linha}")

            # Validar dados fundamentais
            if not linha.get('data') or not linha.get('nCliente') or not linha.get('nrNota'):
                logging.warning(f"⚠️ Falha ao extrair dados fundamentais no Day Trade: {pdf_path}")
                with open("arquivos_sem_texto.txt", "a", encoding="utf-8") as f_out:
                    f_out.write(pdf_path + "\n")
                return False
                
            linhas.append(linha)
            paginas_por_linha.append(list(range(inicio_nota, i + 1)))
            inicio_nota = i + 1
            i += 1

    # Conversão de dicts para modelos SQLModel
    dados = []
    for idx, d in enumerate(linhas):
        try:
            obj = notasDaytrade(**d)
            dados.append(obj)
            # Converte o objeto para dict para o log exibir as chaves/valores
            payload = obj.model_dump() if hasattr(obj, "model_dump") else obj.__dict__
            logging.debug(f"Objeto notasDaytrade [{idx}] montado com sucesso: {payload}")
        except Exception as err:
            logging.error(f"❌ Day Trade {pdf_path}: dados da nota inválidos para notasDaytrade "
                          f"(nota {idx + 1} do arquivo): {err}")
            logging.debug(f"Conteúdo da linha com falha: {d}")
            doc.close()
            return False

    def execute_persistence(session: Session) -> bool:
        if dry_run:
            logging.info(f"🧪 [DRY-RUN] Day Trade PDF simulado com sucesso: {pdf_path}")
            for idx, dado in enumerate(dados):
                payload = dado.model_dump() if hasattr(dado, "model_dump") else dado.__dict__
                logging.info(f"🧪 [DRY-RUN] Registro [{idx}]: {payload}")
            session.rollback()
            return True

        ja_registradas = 0
        for dado in dados: 
            try:
                session.add(dado)
                session.commit()
                payload = dado.model_dump() if hasattr(dado, "model_dump") else dado.__dict__
                logging.info(f"✅ Nota day trade nº {dado.nrNota} ({dado.data}) gravada em notasDaytrade.")
                logging.debug(f"Dado persistido no banco: {payload}")
            except IntegrityError: 
                session.rollback()
                ja_registradas += 1
                logging.info(f"Nota day trade nº {dado.nrNota} ({dado.data}) já estava em notasDaytrade "
                             f"(mesma corretora/CPF/data/conta): nada gravado.")
            except Exception as e: 
                session.rollback()
                logging.error(f"❌ Day Trade {pdf_path}: erro ao gravar em notasDaytrade: {e}") 
                return False
        if resultado is not None:
            resultado['ja_registrada'] = bool(dados) and ja_registradas == len(dados)
        return True

    # Operações por nota, lidas do PDF antes da região crítica (é a parte lenta).
    detalhes = []
    for idx, linha in enumerate(linhas):
        try:
            detalhes.append(extractDayDetalhe.extrair_nota(doc, broker, paginas_por_linha[idx]))
        except Exception as e:
            detalhes.append(e)

    def processar_detalhe(session: Session) -> None:
        """Operações por nota -> operacoesdaytrade_detalhe (só registro, ver
        NOTICE.md). Nunca muda o retorno de process_day_pdf."""
        for idx, linha in enumerate(linhas):
            contexto = f"Day Trade {pdf_path} (nota nº {linha.get('nrNota')}, páginas {paginas_por_linha[idx][0] + 1}-{paginas_por_linha[idx][-1] + 1})"
            try:
                if isinstance(detalhes[idx], Exception):
                    raise detalhes[idx]
                operacoes, conf = detalhes[idx]
                if dry_run:
                    logging.info(f"🧪 [DRY-RUN] {contexto}: {conf['operacoes']} operação(ões) lidas; "
                                 f"soma {conf['soma_operacoes']} = valor dos negócios {conf['valor_negocios']}. Nada gravado.")
                    continue
                nota = extractDayDetalhe.nota_registrada(session, linha)
                if nota is None:
                    raise extractDayDetalhe.ErroDetalheDaytrade("nota não encontrada em notasDaytrade para ligar as operações")
                extractDayDetalhe.gravar_detalhe(session, nota, operacoes, file_hash)
                logging.info(f"{contexto}: {conf['operacoes']} operação(ões) gravadas em operacoesdaytrade_detalhe "
                             f"(soma {conf['soma_operacoes']} = valor dos negócios {conf['valor_negocios']}).")
            except Exception as e:
                logging.error(f"❌ {contexto}: detalhe por operação NÃO gravado: {type(e).__name__}: {e}")
                if not isinstance(e, extractDayDetalhe.ErroDetalheDaytrade):
                    logging.debug("Traceback do detalhe por operação:", exc_info=True)
                if falhas_detalhe is not None:
                    falhas_detalhe.append(f"{contexto}: {e}")

    def persistir(session: Session) -> bool:
        ok = execute_persistence(session)
        if ok:
            processar_detalhe(session)
        return ok

    try:
        with regiao_critica(engine):
            if external_session:
                return persistir(external_session)
            else:
                with get_session() as session:
                    return persistir(session)
    finally:
        doc.close()


if __name__ == "__main__": 
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    directory = Path("/mnt/Projetos/notascorretagem/notas")
    handle_files(directory)
