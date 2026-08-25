import xxhash
from areaCodes import areaDict_btg_day, areaDict_xp_day, brokerName_day
from schemas import * 
from cleanupFunctions import cleanup_dict
import logging
import pymupdf as pymu
from sqlmodel import Session
from db_connection import engine, get_session
from sqlalchemy.exc import IntegrityError
from pathlib import Path

SQLModel.metadata.create_all(engine)


def handle_files(directory, dry_run: bool = False):
    for pdf_path in directory.rglob("*.pdf"):
        logging.info(pdf_path)
        try: 
            process_day_pdf(str(pdf_path), dry_run=dry_run)
        except Exception as e: 
            logging.error(e)
            exit()


def process_day_pdf(pdf_path: str, external_session: Session = None, dry_run: bool = False) -> bool:
    with open(pdf_path, "rb") as f:
        file_hash = xxhash.xxh64(f.read()).hexdigest()

    doc = pymu.open(pdf_path)
    doc_len = len(doc)
    start_page = doc[0]
    coordmap = {}
    broker = ''

    for entry in brokerName_day: 
        bloco = start_page.get_textbox(brokerName_day[entry])
        if "BTG" in bloco: 
            coordmap = areaDict_btg_day
            broker = 'BTG'
            break
        elif "XP" in bloco:
            coordmap = areaDict_xp_day  
            broker = 'XP'
            break

    if not coordmap: 
        logging.error("Impossível Distinguir Corretora.")
        return False
    logging.info(f"broker: {broker}")

    i = 0
    linhas = [] 
    while i < doc_len: 
        page = doc[i]
        bloco = page.get_textbox(coordmap['liquido'])
        if bloco == " |":
            i = i + 1
        else: 
            linha = {}
            linha['corretora'] = broker
            linha['hashNota'] = file_hash
            
            rel_path = str(pdf_path).replace("z:\\", "").replace("Z:\\", "").replace("z:/", "").replace("Z:/", "").replace("/mnt/", "")
            rel_path = rel_path.replace("Projetos\\notascorretagem\\", "").replace("Projetos/notascorretagem/", "")
            linha['relativePath'] = rel_path
            
            for entry in coordmap: 
                if entry == 'continua' or entry not in cleanup_dict:
                    continue
                bloco = page.get_textbox(coordmap[entry])
                bloco = cleanup_dict[entry](bloco)
                linha[entry] = bloco
                
            # Validar dados fundamentais
            if not linha.get('data') or not linha.get('nCliente') or not linha.get('nrNota'):
                logging.warning(f"⚠️  Falha ao extrair dados fundamentais no Day Trade: {pdf_path}")
                with open("arquivos_sem_texto.txt", "a", encoding="utf-8") as f_out:
                    f_out.write(pdf_path + "\n")
                return False
                
            linhas.append(linha)
            i = i + 1

    dados = [notasDaytrade(**dado) for dado in linhas]

    def execute_persistence(session: Session) -> bool:
        if dry_run:
            logging.info(f"🧪 [DRY-RUN] Day Trade PDF simulado com sucesso: {pdf_path} (nenhuma alteração persistida no banco).")
            session.rollback()
            return True

        for dado in dados: 
            try:
                session.add(dado)
                session.commit()
                logging.info(f"dado aceito na base de dados: {dado}")
            except IntegrityError: 
                session.rollback()
                logging.error("dado duplicado :(")
            except Exception as e: 
                session.rollback()
                logging.error(f"python surtou: {e}") 
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    directory = Path("/mnt/Projetos/notascorretagem/notas")
    handle_files(directory)
