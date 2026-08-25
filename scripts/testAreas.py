import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
import fitz as pymu
import pdfplumber as pdfpu
from pymupdf import Rect
from areaCodes import (
    areaDict_day_or_swing,
    areaDict_xp_swing, areaDict_btg_swing,
    areaDict_xp_day, areaDict_btg_day,
    brokerName_swing, brokerName_day
)

def detect_broker_and_trade_type(page_pymu):
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

def test_pdf(pdf_path):
    if not os.path.exists(pdf_path):
        print(f"Arquivo '{pdf_path}' não encontrado.")
        return

    print("=" * 50)
    print(f"TESTANDO ARQUIVO: {pdf_path}")
    print("=" * 50)

    doc = pymu.open(pdf_path)
    with pdfpu.open(pdf_path) as pdf:
        for page_num, (page_pymu, page_pdfpu) in enumerate(zip(doc, pdf.pages), start=1):
            print(f"\n--- PÁGINA {page_num} ---")

            # Executa a detecção em 2 passos
            detected_type, detected_broker = detect_broker_and_trade_type(page_pymu)
            print(f"   => Resultado Detectado: Tipo={detected_type} | Corretora={detected_broker}")

            # Seleciona o dicionário de coordenadas correto
            if detected_type == "SWING":
                coordmap = areaDict_btg_swing if detected_broker == "BTG" else areaDict_xp_swing
            else:
                coordmap = areaDict_btg_day if detected_broker == "BTG" else areaDict_xp_day

            # Extração de Campos de Texto e Resumos
            print(f"\n--- CAMPOS DE TEXTO E RESUMOS ({detected_type} - {detected_broker}) ---")
            for key, val in coordmap.items():
                if isinstance(val, Rect):
                    words = page_pymu.get_text("words", clip=val)
                    lines = defaultdict(list)
                    for w in words:
                        line_y = round(w[1] / 3) * 3
                        lines[line_y].append(w)

                    print(f"\n[{key}]:")
                    if lines:
                        for y in sorted(lines.keys()):
                            words_sorted = sorted(lines[y], key=lambda w: w[0])
                            print(" ".join(w[4] for w in words_sorted))
                    else:
                        print("Nenhum texto encontrado.")

            # Extração de Operações
            if 'tableAreaOperacoes' in coordmap and 'headerArea' in coordmap:
                print(f"\n--- OPERAÇÕES (pdfplumber extract_table) ---")
                area_tuple = coordmap['tableAreaOperacoes']
                
                header_words = page_pymu.get_text("words", clip=coordmap['headerArea'])
                header_words.sort(key=lambda w: w[0])
                
                blocks = []
                for w in header_words:
                    if not blocks or w[0] - blocks[-1][2] > 4:
                        blocks.append(w)
                    else:
                        blocks[-1] = (blocks[-1][0], blocks[-1][1], w[2], w[3], blocks[-1][4] + " " + w[4])
                
                x_range = []
                if blocks:
                    x_range.append(area_tuple[0])
                    for b in blocks[1:]:
                        x_range.append(b[0] - 3)
                    x_range.append(area_tuple[2])
                    x_range = sorted(list(set(x_range)))
                else:
                    print("AVISO: Não foram encontradas palavras na área de cabeçalho. As colunas não puderam ser estimadas.")

                settings = {
                    "vertical_strategy": "explicit",
                    "explicit_vertical_lines": x_range,
                    "horizontal_strategy": "text",
                    "intersection_x_tolerance": 20,
                    "intersection_y_tolerance": 20,
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                    "edge_min_length": 0,
                }
                cropped = page_pdfpu.crop(area_tuple)
                table = cropped.extract_table(settings)
                if table:
                    for row in table:
                        if any(row):
                            print(row)
                else:
                    print("Nenhuma tabela de operações encontrada nesta área.")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Erro: Forneça o caminho para um ou mais arquivos PDF via linha de comando.")
        print("Uso: python testAreas.py <caminho_do_pdf1> [<caminho_do_pdf2> ...]")
        sys.exit(1)
        
    paths = sys.argv[1:]
    for path in paths:
        if os.path.exists(path):
            test_pdf(path)
        else:
            print(f"Erro: Arquivo '{path}' não encontrado.")
