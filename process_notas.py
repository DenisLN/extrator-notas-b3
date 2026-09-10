import os
import re
import fitz as pymu
import xxhash
import logging
import subprocess
import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from sqlmodel import Session, select

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

from areaCodes import (
    areaDict_day_or_swing, areaDict_xp_swing, areaDict_btg_swing,
    areaDict_xp_day, areaDict_btg_day, brokerName_swing, brokerName_day
)
from cleanupFunctions import cleanup_dict
from extractSwing import process_swing_pdf, engine
from extractDay import process_day_pdf
from schemas import operacoesSwingtrade

# URL do banco de dados configurada via .env
DATABASE_URL = os.getenv("DATABASE_URL")

# Configuração de diretórios
INPUT_DIR = Path(os.getenv("INPUT_DIR", "notas"))
_output_dir_env = os.getenv("OUTPUT_DIR")
if _output_dir_env:
    OUTPUT_DIR = Path(_output_dir_env)
else:
    OUTPUT_DIR = (
        Path("Z:/3 Notas Corretagem") if os.path.exists("Z:/3 Notas Corretagem")
        else Path("z:/3 Notas Corretagem") if os.path.exists("z:/3 Notas Corretagem")
        else Path("/mnt/3 Notas Corretagem") if os.path.exists("/mnt/3 Notas Corretagem")
        else Path("./3 Notas Corretagem")
    )

MONTH_MAP = {
    1: "01 - JAN", 2: "02 - FEV", 3: "03 - MAR", 4: "04 - ABR",
    5: "05 - MAI", 6: "06 - JUN", 7: "07 - JUL", 8: "08 - AGO",
    9: "09 - SET", 10: "10 - OUT", 11: "11 - NOV", 12: "12 - DEZ"
}

def detect_note_type(page_pymu) -> tuple[str, str]:
    txt_swing_btg = page_pymu.get_textbox(areaDict_day_or_swing['swing_btg']).strip().upper()
    txt_swing_xp = page_pymu.get_textbox(areaDict_day_or_swing['swing_xp']).strip().upper()
    txt_day_btg = page_pymu.get_textbox(areaDict_day_or_swing['day_btg']).strip().upper()
    txt_day_xp = page_pymu.get_textbox(areaDict_day_or_swing['day_xp']).strip().upper()

    broker = ""
    trade_type = "SWING"

    if "NOTA DE CORRETAGEM" in txt_day_btg or "DAY" in txt_day_btg or "OPERAÇÕES COM ATIVOS" in txt_day_btg:
        broker = "BTG"
        trade_type = "DAY"
    elif "NOTA DE NEGOCIAÇÃO" in txt_day_xp or "DAY" in txt_day_xp or "OPERAÇÕES COM ATIVOS" in txt_day_xp:
        broker = "XP"
        trade_type = "DAY"
    elif "NOTA DE CORRETAGEM" in txt_swing_btg:
        broker = "BTG"
        trade_type = "SWING"
    elif "NOTA DE NEGOCIAÇÃO" in txt_swing_xp:
        broker = "XP"
        trade_type = "SWING"
    else:
        full_text = page_pymu.get_text().upper()
        if "BTG" in full_text or "CORRETAGEM" in txt_swing_btg or "CORRETAGEM" in txt_day_btg:
            broker = "BTG"
        else:
            broker = "XP"

        if "DAY" in full_text or "OPERAÇÕES COM ATIVOS" in full_text or "NOTA DE NEGOCIAÇÃO" in txt_day_xp or "NOTA DE CORRETAGEM" in txt_day_btg:
            trade_type = "DAY"
        else:
            trade_type = "SWING"

    return trade_type, broker

def backup_database():
    try:
        backup_dir = Path("backups")
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"notasDaytrade_backup_{timestamp}.sql"

        logging.info(f"Criando snapshot do banco de dados em '{backup_file}'...")

        process = subprocess.run(
            ["pg_dump", "-c", DATABASE_URL, "-f", str(backup_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if process.returncode == 0:
            logging.info("Snapshot (backup) criado com sucesso.")
            return True
        else:
            logging.error(f"Falha ao criar snapshot do banco (pg_dump): {process.stderr}")
            return False

    except FileNotFoundError:
        logging.error("O comando 'pg_dump' não foi encontrado. O PostgreSQL client (pg_dump) precisa estar instalado e no PATH do sistema.")
        return False
    except Exception as e:
        logging.error(f"Erro inesperado ao criar snapshot: {e}")
        return False

def process_batch(dry_run: bool = False):
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

    if not INPUT_DIR.exists():
        print(f"Diretório de entrada '{INPUT_DIR}' não encontrado.")
        return

    pdf_files = list(INPUT_DIR.rglob("*.pdf"))
    print(f"🔍 {len(pdf_files)} arquivos PDF encontrados em '{INPUT_DIR}'.\n")

    if not pdf_files:
        return

    if dry_run:
        logging.info("🧪 [DRY-RUN ATIVO] MODO SIMULAÇÃO: Nenhum backup será feito, nenhum PDF será apagado/movido e nada será gravado no DB.")
    else:
        logging.info("Iniciando processo de snapshot do banco de dados antes do processamento...")
        if not backup_database():
            logging.warning("⚠️  Processamento abortado porque o snapshot falhou. Para sua segurança, resolva o erro do pg_dump ou comente a chamada de backup.")
            return

    ativos_negociados = set()
    notas_swing = 0
    notas_day = 0

    temp_base_dir = Path("temp")
    temp_base_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(dir=temp_base_dir) as temp_dir:
        base_output_dir = Path(temp_dir) if dry_run else OUTPUT_DIR

        for pdf_path in pdf_files:
            try:
                doc_src = pymu.open(pdf_path)
            except Exception as e:
                logging.error(f"Erro ao abrir PDF {pdf_path}: {e}")
                continue

            if len(doc_src) == 0 or len(doc_src[0].get_text().strip()) < 10:
                logging.warning(f"⚠️  PDF sem texto extraível (escaneado/imagem): {pdf_path.name}")
                doc_src.close()
                continue

            note_pages = []
            doc_len = len(doc_src)
            all_success = True

            for page_idx in range(doc_len):
                page_pymu = doc_src[page_idx]
                note_pages.append(page_idx)

                trade_type, broker = detect_note_type(page_pymu)
                coordmap = (areaDict_xp_swing if broker == "XP" else areaDict_btg_swing) if trade_type == "SWING" else (areaDict_xp_day if broker == "XP" else areaDict_btg_day)

                if broker == "BTG":
                    if trade_type == "DAY" and 'liquido' in coordmap:
                        # Notas Day Trade da BTG não têm a palavra "CONTINUA" em lugar nenhum
                        # (o campo 'continua' do coordmap simplesmente nunca é preenchido nesse
                        # layout). O indicador real de página intermediária é o campo 'liquido'
                        # aparecer vazio, contendo só o separador "|" — mesmo critério usado em
                        # extractDay.py para pular páginas. Só na última página o líquido vem
                        # preenchido com o valor consolidado da nota.
                        liquido_raw = page_pymu.get_textbox(coordmap['liquido']).strip()
                        has_continua = (liquido_raw == "|")
                    elif trade_type == "SWING" and 'tableCustos' in coordmap:
                        has_continua = "CONTINUA" in page_pymu.get_textbox(coordmap['tableCustos']).upper()
                    else:
                        has_continua = "CONTINUA" in page_pymu.get_text().upper()
                else:
                    has_continua = "CONTINUA" in page_pymu.get_text().upper()

                if not has_continua or page_idx == doc_len - 1:
                    start_page_idx = note_pages[0]
                    end_page_idx = note_pages[-1]
                    header_page = doc_src[start_page_idx]

                    n_cliente_raw = header_page.get_textbox(coordmap.get('nCliente', pymu.Rect(0,0,0,0))).strip()
                    n_cliente = cleanup_dict['nCliente'](n_cliente_raw) if n_cliente_raw else None

                    data_raw = header_page.get_textbox(coordmap.get('data', pymu.Rect(0,0,0,0))).strip()
                    data_obj = cleanup_dict['data'](data_raw) if data_raw else None

                    nr_nota_raw = header_page.get_textbox(coordmap.get('nrNota', pymu.Rect(0,0,0,0))).strip() if 'nrNota' in coordmap else ""
                    nr_nota = cleanup_dict['nrNota'](nr_nota_raw) if nr_nota_raw else None
                    n_cliente = str(n_cliente) if n_cliente else "OUTROS"

                    year_str = str(data_obj.year) if (data_obj and hasattr(data_obj, 'year')) else "ANOS_OUTROS"
                    month_str = MONTH_MAP.get(data_obj.month, "MES_OUTRO") if (data_obj and hasattr(data_obj, 'month')) else "MES_OUTRO"
                    date_formatted = data_obj.strftime("%d-%m-%Y") if (data_obj and hasattr(data_obj, 'strftime')) else "01-01-2026"

                    dest_dir = base_output_dir / n_cliente / year_str / month_str
                    dest_dir.mkdir(parents=True, exist_ok=True)

                    prefix = "D@" if trade_type == "DAY" else "S@"
                    isolated_filename = f"{prefix}{date_formatted}@{nr_nota}@{n_cliente}.pdf"
                    isolated_filepath = dest_dir / isolated_filename

                    if not isolated_filepath.exists():
                        new_doc = pymu.open()
                        new_doc.insert_pdf(doc_src, from_page=start_page_idx, to_page=end_page_idx)
                        new_doc.save(str(isolated_filepath))
                        new_doc.close()
                    else:
                        print(f"⏩ Nota já existe no disco: '{isolated_filepath.name}'")

                    if dry_run:
                        logging.info(f"🧪 [DRY-RUN] Isolação de nota simulada para: {isolated_filename}")

                    success = False
                    if trade_type == "SWING":
                        success = process_swing_pdf(str(isolated_filepath), dry_run=dry_run)
                        if success:
                            notas_swing += 1
                            if not dry_run:
                                try:
                                    with Session(engine) as session:
                                        n_nota_int = int(nr_nota) if nr_nota else 0
                                        data_param = data_obj.date() if (hasattr(data_obj, 'date') and callable(getattr(data_obj, 'date'))) else data_obj
                                        
                                        # Consulta as operações persistidas para recuperar os ativos e o CPF correto
                                        ops = session.exec(select(operacoesSwingtrade).where(
                                            operacoesSwingtrade.nCliente == n_cliente,
                                            operacoesSwingtrade.data == data_param,
                                            operacoesSwingtrade.nrNota == n_nota_int
                                        )).all()
                                        
                                        cpf_real = None
                                        for op in ops:
                                            ativos_negociados.add(op.nomeAtivo)
                                            if not cpf_real and op.cpf:
                                                cpf_real = op.cpf
                                        
                                        if cpf_real:
                                            try:
                                                from computeSwing import recalcular_swing
                                                from db_connection import get_session as get_db_session
                                                with get_db_session() as session_compute:
                                                    recalcular_swing(cpf=cpf_real, session=session_compute)
                                            except Exception as e:
                                                logging.error(f"Erro ao recalcular swing para CPF {cpf_real}: {e}")
                                        else:
                                            logging.warning(f"⚠️ CPF não encontrado nas operações registradas da nota {nr_nota} (nCliente: {n_cliente}). Recálculo de swing ignorado.")
                                except Exception as e:
                                    logging.error(f"Erro ao buscar operações no banco e recalcular swing: {e}")
                    else:
                        success = process_day_pdf(str(isolated_filepath), dry_run=dry_run)
                        if success:
                            notas_day += 1

                    if not success:
                        all_success = False

                    note_pages = []

            doc_src.close()

            if all_success and not dry_run:
                try:
                    os.remove(pdf_path)
                    print(f"✅ Arquivo original apagado: {pdf_path}")
                except Exception as e:
                    print(f"Erro ao apagar arquivo original {pdf_path}: {e}")
            elif dry_run:
                print(f"🧪 [DRY-RUN] O arquivo original NÃO foi alterado/removido: {pdf_path}")

    print("\n" + "="*40)
    ativos_str = ", ".join(sorted(list(ativos_negociados))) if ativos_negociados else "Nenhum detectado"
    print(f"resumo: ativos negociados: {ativos_str}")
    print(f"notas swing trade: {notas_swing}")
    print(f"notas day trade: {notas_day}")
    print("="*40 + "\n")

def restore_database(backup_name):
    backup_dir = Path("backups")
    if not backup_dir.exists():
        logging.error("Diretório de backups não encontrado.")
        return False

    if backup_name == 'last':
        backups = list(backup_dir.glob("*.sql"))
        if not backups:
            logging.error("Nenhum backup encontrado na pasta 'backups/'.")
            return False
        backup_file = max(backups, key=lambda p: p.stat().st_mtime)
    else:
        backup_file = backup_dir / backup_name
        if not backup_file.exists():
            backup_file = backup_dir / (backup_name + ".sql")
            if not backup_file.exists():
                logging.error(f"Backup não encontrado: {backup_name}")
                return False

    logging.info(f"Restaurando banco de dados a partir de '{backup_file}'...")

    process = subprocess.run(
        ["psql", DATABASE_URL, "-f", str(backup_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if process.returncode == 0:
        logging.info("Banco de dados restaurado com sucesso.")
        return True
    else:
        logging.error(f"Falha ao restaurar banco de dados (psql): {process.stderr}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Processa notas de corretagem.")
    parser.add_argument("--restore-last", action="store_true", help="Restaura o último backup feito e sai.")
    parser.add_argument("--restore", type=str, metavar="NOME", help="Restaura um backup específico da pasta backups/ e sai.")
    parser.add_argument("--dry-run", action="store_true", help="Executa o fluxo em modo simulação sem alterar o banco de dados nem mover/deletar arquivos.")
    args = parser.parse_args()

    if args.restore_last:
        restore_database('last')
        sys.exit(0)
    elif args.restore:
        restore_database(args.restore)
        sys.exit(0)

    process_batch(dry_run=args.dry_run)
