import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xxhash
from sqlmodel import select

from process_notas import OUTPUT_DIR
from db_connection import get_session
from schemas import notasDaytrade, registroNotasSwing
from extractDay import process_day_pdf
from extractSwing import process_swing_pdf

REPORT_FILE = "notas_ausentes_no_banco.txt"
REPORT_FILE_HASH_DIVERGENTE = "notas_hash_divergente.txt"

# Nome dos arquivos isolados por process_notas.py: "{D@/S@}{dd-mm-aaaa}@{nrNota}@{nCliente}.pdf"
FILENAME_RE = re.compile(r'^(?P<prefix>[DS])@(?P<date>\d{2}-\d{2}-\d{4})@(?P<nrnota>\d+)@(?P<ncliente>.+)\.pdf$')


def compute_hash(pdf_path: Path) -> str:
    with open(pdf_path, "rb") as f:
        return xxhash.xxh64(f.read()).hexdigest()


def load_known_hashes() -> set[str]:
    with get_session() as session:
        day_hashes = session.exec(select(notasDaytrade.hashNota)).all()
        swing_hashes = session.exec(select(registroNotasSwing.hashNota)).all()
    return set(day_hashes) | set(swing_hashes)


def parse_isolated_filename(pdf_path: Path):
    """Extrai (trade_type, data, nrNota, nCliente) do nome do arquivo isolado.
    Retorna None se o nome não seguir a convenção de process_notas.py
    (ex.: arquivo colocado manualmente em OUTPUT_DIR)."""
    m = FILENAME_RE.match(pdf_path.name)
    if not m:
        return None
    trade_type = 'DAY' if m.group('prefix') == 'D' else 'SWING'
    data_obj = datetime.strptime(m.group('date'), '%d-%m-%Y').date()
    nr_nota = int(m.group('nrnota'))
    n_cliente = m.group('ncliente')
    return trade_type, data_obj, nr_nota, n_cliente


def find_existing_record(session, trade_type, data_obj, nr_nota, n_cliente):
    """Verifica se já existe um registro lógico (nrNota+data+nCliente) no banco,
    independente do hash do arquivo atual -- é isso que permite distinguir uma
    nota REALMENTE ausente de uma nota cujo arquivo em disco mudou de conteúdo
    (e portanto de hash) depois de já ter sido processada."""
    if trade_type == 'DAY':
        stmt = select(notasDaytrade).where(
            notasDaytrade.nrNota == nr_nota,
            notasDaytrade.data == data_obj,
            notasDaytrade.nCliente == n_cliente,
        )
    else:
        stmt = select(registroNotasSwing).where(
            registroNotasSwing.nrNota == nr_nota,
            registroNotasSwing.data == data_obj,
            registroNotasSwing.nCliente == n_cliente,
        )
    return session.exec(stmt).first()


def classify_missing(missing: list[Path]):
    """Para cada arquivo ausente por hash, classifica em:
    - ja_no_banco: existe um registro lógico no banco, mas com hash diferente
      do arquivo atual (arquivo mudou de conteúdo depois de processado).
    - realmente_ausentes: nenhum registro lógico encontrado -- candidata real
      a ser extraída/gravada.
    - nao_reconhecidas: nome de arquivo fora do padrão de isolamento -- não dá
      pra classificar com segurança sem reprocessar manualmente.
    """
    ja_no_banco = []
    realmente_ausentes = []
    nao_reconhecidas = []
    with get_session() as session:
        for pdf_path in missing:
            parsed = parse_isolated_filename(pdf_path)
            if not parsed:
                nao_reconhecidas.append(pdf_path)
                continue
            trade_type, data_obj, nr_nota, n_cliente = parsed
            existing = find_existing_record(session, trade_type, data_obj, nr_nota, n_cliente)
            if existing:
                ja_no_banco.append((pdf_path, trade_type, existing.hashNota))
            else:
                realmente_ausentes.append((pdf_path, trade_type))
    return ja_no_banco, realmente_ausentes, nao_reconhecidas


def process_realmente_ausentes(realmente_ausentes: list[tuple[Path, str]], apply: bool):
    """Roda o extrator de verdade (dry_run=not apply) em cada nota confirmada
    como realmente ausente. Sem --apply, apenas simula (nada é gravado)."""
    results = []
    for pdf_path, trade_type in realmente_ausentes:
        fn = process_day_pdf if trade_type == 'DAY' else process_swing_pdf
        try:
            success = fn(str(pdf_path), dry_run=not apply)
        except Exception as e:
            print(f"❌ Erro ao processar '{pdf_path}': {e}")
            success = False
        results.append((pdf_path, trade_type, success))
    return results


def move_file(pdf_path: Path, base_dir: Path, subfolder: str) -> Path | None:
    dest_dir = base_dir / subfolder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / pdf_path.name
    try:
        shutil.move(str(pdf_path), str(dest_path))
        return dest_path
    except Exception as e:
        print(f"⚠️  Erro ao mover '{pdf_path}' para '{dest_path}': {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Audita as notas em OUTPUT_DIR contra o banco de dados.")
    parser.add_argument(
        "--recheck", action="store_true",
        help="Para cada nota ausente por hash, verifica se já existe um registro lógico "
             "(nrNota+data+nCliente) no banco antes de considerá-la realmente ausente "
             "(implícito quando --apply é usado)."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Extrai e GRAVA de verdade as notas confirmadas como realmente ausentes. "
             "Sem esta flag (mas com --recheck), roda o extrator em modo dry-run apenas para prévia."
    )
    parser.add_argument(
        "--move-to", type=str, metavar="DIR",
        help="Move as notas já classificadas para subpastas dentro de DIR: "
             "'ja_no_banco_hash_divergente/' para as que já existem no banco com hash diferente, "
             "e 'processadas_agora/' para as que foram gravadas de verdade nesta execução (requer --apply)."
    )
    args = parser.parse_args()
    if args.apply:
        args.recheck = True
    if args.move_to:
        args.recheck = True

    if not OUTPUT_DIR.exists():
        print(f"Diretório de saída '{OUTPUT_DIR}' não encontrado.")
        return

    pdf_files = sorted(OUTPUT_DIR.rglob("*.pdf"))
    print(f"🔍 {len(pdf_files)} arquivo(s) PDF encontrados em '{OUTPUT_DIR}'.\n")

    known_hashes = load_known_hashes()
    print(f"📚 {len(known_hashes)} hash(es) de notas encontrados no banco de dados.\n")

    missing = []
    for pdf_path in pdf_files:
        try:
            file_hash = compute_hash(pdf_path)
        except Exception as e:
            print(f"⚠️  Erro ao ler '{pdf_path}': {e}")
            continue
        if file_hash not in known_hashes:
            missing.append(pdf_path)

    print("=" * 40)
    if not missing:
        print("✅ Todas as notas em OUTPUT_DIR estão presentes no banco de dados.")
        print("=" * 40)
        return

    print(f"🚨 {len(missing)} nota(s) encontradas em '{OUTPUT_DIR}' que NÃO batem com nenhum hash do banco:\n")
    for path in missing:
        print(f"  - {path}")
    with open(REPORT_FILE, "w", encoding="utf-8") as f_out:
        for path in missing:
            f_out.write(f"{path}\n")
    print(f"\n📝 Lista completa gravada em '{REPORT_FILE}'.")
    print("=" * 40)

    if not args.recheck:
        return

    move_base = Path(args.move_to) if args.move_to else None

    ja_no_banco, realmente_ausentes, nao_reconhecidas = classify_missing(missing)

    print("\n" + "=" * 40)
    print(f"🔎 Reclassificação (--recheck):")
    print(f"   já existem no banco, mas com hash divergente: {len(ja_no_banco)}")
    print(f"   realmente ausentes:                           {len(realmente_ausentes)}")
    print(f"   nome fora do padrão (não classificadas):      {len(nao_reconhecidas)}")
    print("=" * 40)

    if ja_no_banco:
        print(f"\n⚠️  {len(ja_no_banco)} nota(s) JÁ EXISTEM no banco (mesmo nrNota/data/nCliente), "
              f"mas o arquivo atual tem hash diferente do gravado -- o arquivo em disco mudou de "
              f"conteúdo depois de processado:\n")
        with open(REPORT_FILE_HASH_DIVERGENTE, "w", encoding="utf-8") as f_out:
            for pdf_path, trade_type, hash_no_banco in ja_no_banco:
                print(f"  - [{trade_type}] {pdf_path} (hash no banco: {hash_no_banco})")
                f_out.write(f"{pdf_path} | hash_no_banco={hash_no_banco}\n")
                if move_base:
                    dest = move_file(pdf_path, move_base, "ja_no_banco_hash_divergente")
                    if dest:
                        print(f"    ↳ movida para '{dest}'")
        print(f"\n📝 Lista gravada em '{REPORT_FILE_HASH_DIVERGENTE}'.")

    if nao_reconhecidas:
        print(f"\n❓ {len(nao_reconhecidas)} arquivo(s) com nome fora do padrão de isolamento "
              f"(não foi possível classificar automaticamente, revise manualmente):\n")
        for pdf_path in nao_reconhecidas:
            print(f"  - {pdf_path}")

    if realmente_ausentes:
        modo = "GRAVANDO DE VERDADE (--apply)" if args.apply else "SIMULAÇÃO (dry-run, use --apply para gravar de verdade)"
        print(f"\n🆕 {len(realmente_ausentes)} nota(s) realmente ausentes -- processando em modo {modo}:\n")
        results = process_realmente_ausentes(realmente_ausentes, apply=args.apply)
        for pdf_path, trade_type, success in results:
            status = "✅ OK" if success else "❌ FALHOU"
            print(f"  - [{trade_type}] {pdf_path} -> {status}")
            if success and args.apply and move_base:
                dest = move_file(pdf_path, move_base, "processadas_agora")
                if dest:
                    print(f"    ↳ movida para '{dest}'")


if __name__ == "__main__":
    main()
