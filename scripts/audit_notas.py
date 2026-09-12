import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
import xxhash
from sqlmodel import select

from process_notas import OUTPUT_DIR
from db_connection import get_session
from schemas import notasDaytrade, registroNotasSwing

REPORT_FILE = "notas_ausentes_no_banco.txt"


def compute_hash(pdf_path: Path) -> str:
    with open(pdf_path, "rb") as f:
        return xxhash.xxh64(f.read()).hexdigest()


def load_known_hashes() -> set[str]:
    with get_session() as session:
        day_hashes = session.exec(select(notasDaytrade.hashNota)).all()
        swing_hashes = session.exec(select(registroNotasSwing.hashNota)).all()
    return set(day_hashes) | set(swing_hashes)


def main():
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
    if missing:
        print(f"🚨 {len(missing)} nota(s) encontradas em '{OUTPUT_DIR}' que NÃO estão no banco de dados:\n")
        for path in missing:
            print(f"  - {path}")
        with open(REPORT_FILE, "w", encoding="utf-8") as f_out:
            for path in missing:
                f_out.write(f"{path}\n")
        print(f"\n📝 Lista completa gravada em '{REPORT_FILE}'.")
    else:
        print("✅ Todas as notas em OUTPUT_DIR estão presentes no banco de dados.")
    print("=" * 40)


if __name__ == "__main__":
    main()
