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

SQLModel.metadata.create_all(engine)


def handle_files(directory, dry_run: bool = False):
    for pdf_path in directory.rglob("*.pdf"):
        logging.info(f"📁 Processando arquivo: {pdf_path}")
        try: 
            process_day_pdf(str(pdf_path), dry_run=dry_run)
        except Exception as e: 
            logging.exception(f"💥 Erro fatal ao processar {pdf_path}: {e}")
            exit()


def process_day_pdf(pdf_path: str, external_session: Session = None, dry_run: bool = False) -> bool:
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
            logging.error(f"Erro ao instanciar notasDaytrade para a linha {idx}: {err}")
            logging.debug(f"Conteúdo da linha com falha: {d}")

    def execute_persistence(session: Session) -> bool:
        if dry_run:
            logging.info(f"🧪 [DRY-RUN] Day Trade PDF simulado com sucesso: {pdf_path}")
            for idx, dado in enumerate(dados):
                payload = dado.model_dump() if hasattr(dado, "model_dump") else dado.__dict__
                logging.info(f"🧪 [DRY-RUN] Registro [{idx}]: {payload}")
            session.rollback()
            return True

        for dado in dados: 
            try:
                session.add(dado)
                session.commit()
                payload = dado.model_dump() if hasattr(dado, "model_dump") else dado.__dict__
                logging.info(f"✅ Dado persistido no banco: {payload}")
            except IntegrityError: 
                session.rollback()
                logging.warning(f"⚠️ Dado duplicado retido por constraint: nrNota/hashNota")
            except Exception as e: 
                session.rollback()
                logging.error(f"❌ Erro ao gravar no banco: {e}") 
                return False
        return True

    try:
        if external_session:
            return execute_persistence(external_session)
        else:
            with get_session() as session:
                return execute_persistence(session)
    finally:
        doc.close()


if __name__ == "__main__": 
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    directory = Path("/mnt/Projetos/notascorretagem/notas")
    handle_files(directory)
