import os
import re
import json
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
# extractSwing/extractDay (e o banco) só são importados no subprocesso que
# processa cada arquivo (ver processar_um_arquivo): o processo principal só
# trava, lista a entrada, faz o snapshot e junta os resultados.

# Códigos de saída (o coletor de e-mails grava o código no journal).
SAIDA_OK = 0        # tudo processado (ou nada a processar)
SAIDA_FALHA = 1     # alguma falha: nota com erro (ou gravada com ERROR no log), PDF ilegível, pg_dump, detalhe de day trade, erro inesperado
SAIDA_PAUSADA = 2   # nenhuma falha, mas nota(s) pausada(s) esperando missing_assets.yaml
SAIDA_OCUPADO = 3   # outra execução tem a trava INPUT_DIR/.processando; nada feito
DESCRICAO_SAIDA = {
    SAIDA_OK: "ok",
    SAIDA_FALHA: "houve falha",
    SAIDA_PAUSADA: "pausado: preencha missing_assets.yaml",
    SAIDA_OCUPADO: "outra execução em andamento",
}
YAML_PENDENTES = "missing_assets.yaml"   # o mesmo de assetMapper.YAML_PATH

# Cada PDF da entrada é processado num subprocesso (este mesmo script com
# --um-arquivo): uma nota que quebre o interpretador, trave ou vaze memória
# não derruba o lote. O subprocesso termina a saída com uma linha
# MARCA_RESULTADO + JSON, que o processo principal lê para montar o resumo.
MARCA_RESULTADO = "@@RESULTADO_ARQUIVO@@ "
TIMEOUT_POR_ARQUIVO = int(os.getenv("NOTAS_TIMEOUT_POR_ARQUIVO", "1800"))  # segundos

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

class _UltimoAviso(logging.Handler):
    """Guarda a última mensagem WARNING/ERROR emitida -- usada no resumo final
    para dizer POR QUE uma nota falhou sem repetir o log inteiro -- e os
    ERRORs emitidos desde o último reiniciar(), para que um erro registrado
    durante uma nota "bem-sucedida" não passe em silêncio."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.reiniciar()

    def reiniciar(self):
        self.ultima = None
        self.erros = []

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        self.ultima = msg
        if record.levelno >= logging.ERROR:
            self.erros.append(msg)


class Resumo:
    def __init__(self):
        self.arquivos = 0
        self.gravadas = {"SWING": 0, "DAY": 0}
        self.ja_registradas = {"SWING": 0, "DAY": 0}
        self.falhas = []          # (arquivo, descrição)
        self.pausadas = []        # (arquivo, descrição) -- missing_assets.yaml
        self.falhas_detalhe = []  # detalhe por operação de day trade (só registro)
        self.com_erros = []       # (arquivo, descrição) -- gravada, mas com ERROR no log
        self.originais_apagados = 0
        self.originais_mantidos = []
        self.ativos_negociados = set()
        self.erro_geral = None    # falha que impediu o lote (pg_dump etc.)

    def para_json(self, all_success: bool) -> str:
        return json.dumps({
            "all_success": all_success,
            "gravadas": self.gravadas,
            "ja_registradas": self.ja_registradas,
            "falhas": self.falhas,
            "pausadas": self.pausadas,
            "falhas_detalhe": self.falhas_detalhe,
            "com_erros": self.com_erros,
            "ativos_negociados": sorted(self.ativos_negociados),
        }, ensure_ascii=False)

    def juntar(self, dados: dict):
        for tipo in ("SWING", "DAY"):
            self.gravadas[tipo] += dados["gravadas"].get(tipo, 0)
            self.ja_registradas[tipo] += dados["ja_registradas"].get(tipo, 0)
        self.falhas += [tuple(x) for x in dados["falhas"]]
        self.pausadas += [tuple(x) for x in dados["pausadas"]]
        self.falhas_detalhe += dados["falhas_detalhe"]
        self.com_erros += [tuple(x) for x in dados["com_erros"]]
        self.ativos_negociados |= set(dados["ativos_negociados"])

    def codigo_saida(self) -> int:
        if self.erro_geral or self.falhas or self.falhas_detalhe or self.com_erros:
            return SAIDA_FALHA
        if self.pausadas:
            return SAIDA_PAUSADA
        return SAIDA_OK

    def imprimir(self, dry_run: bool):
        L = logging.getLogger("resumo")
        L.info("=" * 60)
        L.info(f"RESUMO{' (DRY-RUN: nada gravado nem apagado)' if dry_run else ''}")
        if self.erro_geral:
            L.error(f"  lote NÃO processado: {self.erro_geral}")
        L.info(f"  arquivos PDF lidos: {self.arquivos}")
        L.info(f"  notas processadas (gravadas): swing {self.gravadas['SWING']}, day trade {self.gravadas['DAY']}")
        L.info(f"  notas já existentes no banco: swing {self.ja_registradas['SWING']}, day trade {self.ja_registradas['DAY']}")
        L.info(f"  notas com falha: {len(self.falhas)}; gravadas mas com erro no log: {len(self.com_erros)}; "
               f"pausadas por missing_assets.yaml: {len(self.pausadas)}")
        if not dry_run:
            L.info(f"  originais apagados da entrada: {self.originais_apagados}; mantidos (para nova tentativa): {len(self.originais_mantidos)}")
        ativos = ", ".join(sorted(self.ativos_negociados)) if self.ativos_negociados else "nenhum detectado"
        L.info(f"  ativos negociados (swing): {ativos}")
        for arq, desc in self.falhas[:10]:
            L.error(f"  FALHA {arq}: {desc}")
        if len(self.falhas) > 10:
            L.error(f"  ... e mais {len(self.falhas) - 10} falha(s) (veja o log acima)")
        for arq, desc in self.com_erros[:5]:
            L.error(f"  GRAVADA COM ERRO {arq}: {desc}")
        for arq, desc in self.pausadas[:5]:
            L.warning(f"  PAUSADA {arq}: {desc}")
        if self.falhas_detalhe:
            L.error(f"  detalhe por operação de day trade NÃO gravado em {len(self.falhas_detalhe)} nota(s) "
                    f"(as notas em si foram gravadas; ver NOTICE.md):")
            for desc in self.falhas_detalhe[:5]:
                L.error(f"    {desc}")
        codigo = self.codigo_saida()
        L.info(f"  código de saída: {codigo} ({DESCRICAO_SAIDA[codigo]})")
        L.info("=" * 60)


def ativos_pendentes() -> list[str]:
    """Nomes de ativo ainda sem ticker em missing_assets.yaml (gerado por
    assetMapper.py no diretório atual)."""
    if not os.path.exists(YAML_PENDENTES):
        return []
    try:
        import yaml
        with open(YAML_PENDENTES, "r", encoding="utf-8") as f:
            dados = yaml.safe_load(f) or {}
        return [k for k, v in dados.items() if not v or not str(v).strip()]
    except Exception as e:
        logging.warning(f"Não consegui ler {YAML_PENDENTES}: {e}")
        return []


def nota_ja_registrada(trade_type: str, n_cliente, data_obj, nr_nota) -> bool:
    """A nota (conta, data, número) já está no banco? Só leitura; serve para
    o resumo distinguir "já existente" de "gravada agora"."""
    if not (n_cliente and data_obj and nr_nota):
        return False
    from extractSwing import engine
    from schemas import notasDaytrade, registroNotasSwing
    tabela = notasDaytrade if trade_type == "DAY" else registroNotasSwing
    with Session(engine) as session:
        return session.exec(select(tabela.id).where(
            tabela.nCliente == str(n_cliente),
            tabela.data == data_obj,
            tabela.nrNota == int(nr_nota),
        )).first() is not None


def process_batch(dry_run: bool = False, resumo: "Resumo | None" = None) -> Resumo:
    resumo = resumo if resumo is not None else Resumo()

    pdf_files = list(INPUT_DIR.rglob("*.pdf"))
    logging.info(f"🔍 {len(pdf_files)} arquivo(s) PDF em '{INPUT_DIR}'.")

    if not pdf_files:
        return resumo

    if dry_run:
        logging.info("🧪 [DRY-RUN ATIVO] MODO SIMULAÇÃO: Nenhum backup será feito, nenhum PDF será apagado/movido e nada será gravado no DB.")
    else:
        logging.info("Iniciando processo de snapshot do banco de dados antes do processamento...")
        if not backup_database():
            resumo.erro_geral = ("o snapshot do banco (pg_dump) falhou; nada foi processado. "
                                 "Resolva o erro do pg_dump (mensagem acima) e rode de novo.")
            return resumo

    temp_base_dir = Path("temp")
    temp_base_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(dir=temp_base_dir) as temp_dir:
        base_output_dir = Path(temp_dir) if dry_run else OUTPUT_DIR

        for n_arq, pdf_path in enumerate(pdf_files, start=1):
            resumo.arquivos += 1
            logging.info(f"── Arquivo {n_arq}/{len(pdf_files)}: {pdf_path.name}")
            all_success = rodar_subprocesso(pdf_path, base_output_dir, dry_run, resumo)

            if dry_run:
                logging.info(f"🧪 [DRY-RUN] O arquivo original NÃO foi alterado/removido: {pdf_path}")
            elif all_success:
                try:
                    os.remove(pdf_path)
                    resumo.originais_apagados += 1
                    logging.info(f"✅ Arquivo original apagado: {pdf_path}")
                except Exception as e:
                    logging.error(f"❌ Erro ao apagar arquivo original {pdf_path}: {e}")
                    resumo.falhas.append((pdf_path.name, f"processado, mas o original não pôde ser apagado: {e}"))
            else:
                resumo.originais_mantidos.append(pdf_path.name)
                logging.warning(f"⚠️  {pdf_path.name}: mantido na entrada (alguma nota falhou ou foi pausada); será tentado de novo na próxima execução.")

    return resumo


def rodar_subprocesso(pdf_path: Path, base_output_dir: Path, dry_run: bool, resumo: Resumo) -> bool:
    """Processa um PDF num subprocesso (este script com --um-arquivo), repassa
    a saída dele para o log e junta o resultado ao resumo. Devolve True se
    todas as notas do arquivo foram gravadas (ou já existiam)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "--um-arquivo", str(pdf_path),
           "--saida", str(base_output_dir)]
    if dry_run:
        cmd.append("--dry-run")
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        cmd.append("--verbose")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT_POR_ARQUIVO, env=env)
    except subprocess.TimeoutExpired as e:
        _repassar(e.stdout, e.stderr)
        logging.error(f"❌ {pdf_path.name}: o subprocesso passou de {TIMEOUT_POR_ARQUIVO} s e foi encerrado.")
        resumo.falhas.append((pdf_path.name, f"subprocesso passou de {TIMEOUT_POR_ARQUIVO} s (encerrado)"))
        return False
    except Exception as e:
        logging.exception(f"❌ {pdf_path.name}: não consegui iniciar o subprocesso: {e}")
        resumo.falhas.append((pdf_path.name, f"não consegui iniciar o subprocesso: {e}"))
        return False

    dados = _repassar(r.stdout, r.stderr)
    if dados is None:
        ultima = next((l for l in reversed((r.stderr or "").splitlines()) if l.strip()), "")
        desc = f"subprocesso terminou com código {r.returncode} sem resultado" + (f" ({ultima})" if ultima else "")
        logging.error(f"❌ {pdf_path.name}: {desc}")
        resumo.falhas.append((pdf_path.name, desc))
        return False
    resumo.juntar(dados)
    return bool(dados["all_success"])


def _repassar(stdout, stderr):
    """Copia a saída do subprocesso para a do processo principal (o log de
    cada nota continua aparecendo, em ordem) e devolve o JSON do resultado."""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", "replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    dados = None
    for linha in (stdout or "").splitlines():
        if linha.startswith(MARCA_RESULTADO):
            try:
                dados = json.loads(linha[len(MARCA_RESULTADO):])
            except ValueError as e:
                logging.error(f"Resultado do subprocesso ilegível: {e}")
            continue
        print(linha, flush=True)
    for linha in (stderr or "").splitlines():
        if linha.strip():
            print(f"[stderr] {linha}", flush=True)
    return dados


def processar_um_arquivo(pdf_path: Path, base_output_dir: Path, dry_run: bool) -> int:
    """Modo subprocesso (--um-arquivo): processa um PDF e imprime o resultado
    numa linha MARCA_RESULTADO + JSON. Não pega a trava, não faz snapshot e
    não apaga o original (tudo isso é do processo principal)."""
    resumo = Resumo()
    ultimo_aviso = _UltimoAviso()
    logging.getLogger().addHandler(ultimo_aviso)
    try:
        all_success = process_file(pdf_path, base_output_dir, dry_run, resumo, ultimo_aviso)
    except Exception as e:
        logging.exception(f"❌ {pdf_path.name}: erro inesperado ao processar o arquivo: {type(e).__name__}: {e}")
        resumo.falhas.append((pdf_path.name, f"erro inesperado: {type(e).__name__}: {e}"))
        all_success = False
    sys.stdout.flush()
    print(MARCA_RESULTADO + resumo.para_json(all_success), flush=True)
    return resumo.codigo_saida()


def process_file(pdf_path: Path, base_output_dir: Path, dry_run: bool, resumo: Resumo, ultimo_aviso: _UltimoAviso) -> bool:
    """Isola cada nota do PDF e processa. Devolve True se todas as notas do
    arquivo foram gravadas (ou já existiam) -- só então o original é apagado."""
    from extractSwing import process_swing_pdf
    from extractDay import process_day_pdf
    try:
        doc_src = pymu.open(pdf_path)
    except Exception as e:
        logging.error(f"❌ {pdf_path.name}: não abre como PDF: {e}")
        resumo.falhas.append((pdf_path.name, f"não abre como PDF: {e}"))
        return False

    try:
        if doc_src.needs_pass:
            logging.error(f"❌ {pdf_path.name}: PDF protegido por senha (o coletor deveria entregá-lo já sem senha).")
            resumo.falhas.append((pdf_path.name, "PDF protegido por senha"))
            return False
        if len(doc_src) == 0 or len(doc_src[0].get_text().strip()) < 10:
            logging.error(f"❌ {pdf_path.name}: PDF sem texto extraível (escaneado/imagem ou ainda com senha).")
            resumo.falhas.append((pdf_path.name, "PDF sem texto extraível (escaneado/imagem ou com senha)"))
            return False

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
                rotulo = f"{pdf_path.name} [nota nº {nr_nota}, {trade_type}, {broker}, págs. {start_page_idx + 1}-{end_page_idx + 1}]"
                logging.info(f"{rotulo}: isolada como '{isolated_filename}'.")

                if not isolated_filepath.exists():
                    new_doc = pymu.open()
                    new_doc.insert_pdf(doc_src, from_page=start_page_idx, to_page=end_page_idx)
                    new_doc.save(str(isolated_filepath))
                    new_doc.close()
                else:
                    logging.info(f"⏩ Nota já existe no disco: '{isolated_filepath.name}'")

                if dry_run:
                    logging.info(f"🧪 [DRY-RUN] Isolação de nota simulada para: {isolated_filename}")

                try:
                    ja_registrada = nota_ja_registrada(trade_type, n_cliente, data_obj, nr_nota)
                except Exception as e:
                    logging.warning(f"{rotulo}: não consegui conferir se a nota já está no banco: {e}")
                    ja_registrada = False

                ultimo_aviso.reiniciar()
                success = False
                motivo = None
                try:
                    if trade_type == "SWING":
                        success = process_swing_pdf(str(isolated_filepath), dry_run=dry_run)
                        if success and not dry_run:
                            try:
                                recalcular_swing_da_nota(n_cliente, data_obj, nr_nota, resumo)
                            except Exception as e:
                                logging.exception(f"❌ {rotulo}: nota gravada, mas o recálculo do swing falhou: {e}")
                                success = False
                                motivo = f"nota gravada, mas o recálculo do swing falhou: {e} (o original fica para refazer o recálculo)"
                    else:
                        success = process_day_pdf(str(isolated_filepath), dry_run=dry_run,
                                                  falhas_detalhe=resumo.falhas_detalhe)
                except Exception as e:
                    # Uma nota com erro não derruba as outras do mesmo arquivo nem o lote.
                    logging.exception(f"❌ {rotulo}: erro inesperado ao processar a nota: {type(e).__name__}: {e}")
                    success = False
                    motivo = f"erro inesperado: {type(e).__name__}: {e}"

                if success:
                    erros = [m for m in ultimo_aviso.erros if "detalhe por operação" not in m]
                    if erros:
                        resumo.com_erros.append((pdf_path.name, f"nota nº {nr_nota} ({trade_type}): {erros[-1]}"
                                                 + (f" (+{len(erros) - 1} erro(s))" if len(erros) > 1 else "")))
                    if ja_registrada:
                        resumo.ja_registradas[trade_type] += 1
                        logging.info(f"✔️  {rotulo}: já existia no banco (nada duplicado).")
                    else:
                        resumo.gravadas[trade_type] += 1
                        logging.info(f"✔️  {rotulo}: {'simulada' if dry_run else 'gravada'}.")
                else:
                    all_success = False
                    pausada = (trade_type == "SWING" and motivo is None
                               and YAML_PENDENTES in (ultimo_aviso.ultima or ""))
                    if pausada:
                        pendentes = ativos_pendentes()
                        desc = (f"ativo(s) sem ticker: {', '.join(pendentes) or '?'} -- "
                                f"preencha {os.path.abspath(YAML_PENDENTES)} e rode de novo")
                        resumo.pausadas.append((pdf_path.name, f"nota nº {nr_nota}: {desc}"))
                        logging.warning(f"⏸️  {rotulo}: pausada; {desc}")
                    else:
                        desc = motivo or ultimo_aviso.ultima or "falhou (veja as mensagens acima)"
                        resumo.falhas.append((pdf_path.name, f"nota nº {nr_nota} ({trade_type}): {desc}"))
                        logging.error(f"❌ {rotulo}: falhou: {desc}")

                note_pages = []

        return all_success
    finally:
        doc_src.close()


def recalcular_swing_da_nota(n_cliente, data_obj, nr_nota, resumo: Resumo):
    """Recupera as operações gravadas da nota (ativos e CPF) e recalcula o
    swing do CPF (computeSwing.recalcular_swing). Exceções sobem."""
    from extractSwing import engine
    from schemas import operacoesSwingtrade
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
            resumo.ativos_negociados.add(op.nomeAtivo)
            if not cpf_real and op.cpf:
                cpf_real = op.cpf

    if cpf_real:
        from computeSwing import recalcular_swing
        from db_connection import get_session as get_db_session
        with get_db_session() as session_compute:
            recalcular_swing(cpf=cpf_real, session=session_compute)
    else:
        logging.warning(f"⚠️ CPF não encontrado nas operações registradas da nota {nr_nota} (conta {n_cliente}). "
                        f"Recálculo de swing ignorado (normal se a nota só tinha ativos da lista de exclusão).")

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

def configurar_log(verbose: bool = False):
    """Tudo no stdout (o coletor de e-mails junta stdout+stderr nessa ordem e
    guarda as últimas 30 linhas: com o log no stdout, o resumo fica no fim)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    raiz.addHandler(handler)
    # extractSwing.py põe a raiz em DEBUG ao ser importado; aqui decide-se de vez.
    raiz.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("pdfminer").setLevel(logging.WARNING)


def adquirir_trava(diretorio: Path):
    """Trava exclusiva em <INPUT_DIR>/.processando para impedir duas
    execuções ao mesmo tempo sobre os mesmos PDFs. Devolve o arquivo aberto
    (manter aberto até o fim) ou None se outra execução já tem a trava. O
    arquivo fica no disco de propósito: apagá-lo abriria uma corrida."""
    caminho = diretorio / ".processando"
    f = open(caminho, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()} desde {datetime.now().isoformat(timespec='seconds')}\n")
    f.flush()
    return f


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Processa notas de corretagem.",
        epilog="Códigos de saída: 0 = tudo certo; 1 = alguma falha (nota com erro ou gravada com ERROR no log, pg_dump, "
               "detalhe de day trade, erro inesperado); 2 = só pausas por missing_assets.yaml; "
               "3 = outra execução em andamento (nada feito).")
    parser.add_argument("--restore-last", action="store_true", help="Restaura o último backup feito e sai.")
    parser.add_argument("--restore", type=str, metavar="NOME", help="Restaura um backup específico da pasta backups/ e sai.")
    parser.add_argument("--dry-run", action="store_true", help="Executa o fluxo em modo simulação sem alterar o banco de dados nem mover/deletar arquivos.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log em nível DEBUG.")
    # Uso interno: o processo principal chama este script uma vez por PDF.
    parser.add_argument("--um-arquivo", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--saida", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    configurar_log(args.verbose)

    if args.um_arquivo:
        return processar_um_arquivo(args.um_arquivo, args.saida or OUTPUT_DIR, args.dry_run)

    if args.restore_last:
        return SAIDA_OK if restore_database('last') else SAIDA_FALHA
    elif args.restore:
        return SAIDA_OK if restore_database(args.restore) else SAIDA_FALHA

    if not INPUT_DIR.is_dir():
        logging.error(f"❌ Diretório de entrada '{INPUT_DIR}' não encontrado (INPUT_DIR no .env).")
        return SAIDA_FALHA

    trava = adquirir_trava(INPUT_DIR)
    if trava is None:
        logging.error(f"⛔ Outra execução do process_notas.py já está em andamento (trava em "
                      f"'{INPUT_DIR / '.processando'}'). Nada foi feito; os PDFs ficam para a próxima.")
        return SAIDA_OCUPADO

    resumo = Resumo()
    try:
        process_batch(dry_run=args.dry_run, resumo=resumo)
    except Exception as e:
        logging.exception(f"❌ Erro inesperado no lote: {type(e).__name__}: {e}")
        resumo.erro_geral = f"erro inesperado: {type(e).__name__}: {e}"
    finally:
        trava.close()

    resumo.imprimir(args.dry_run)
    return resumo.codigo_saida()


if __name__ == "__main__":
    sys.exit(main())
