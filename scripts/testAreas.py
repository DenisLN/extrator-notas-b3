import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
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


# ---------------------------------------------------------------------------
# ITEM 1: detecção de colunas por matching de texto contra coordmap['headers']
# ---------------------------------------------------------------------------
def build_x_ranges_by_header_match(header_words, expected_headers, area_left, area_right):
    """
    Em vez de decidir bordas de coluna por distância (gap) entre palavras,
    casa sequencialmente as palavras extraídas do headerArea contra a lista
    canônica coordmap['headers']. Cada header esperado pode ser formado por
    várias palavras (ex.: "Tipo mercado", "Preço / Ajuste") — vamos
    acumulando palavras normalizadas até fechar exatamente o texto esperado.

    Isso é imune a mudanças de fonte/kerning/espaçamento, desde que o TEXTO
    dos headers continue o mesmo (que é o caso: a XP só apertou a fonte).

    Retorna: (x_ranges | None, ok: bool, debug_rows: list)
    debug_rows = [(palavra, x0, y0, x1, y1, status), ...]
    """
    expected_norm = [re.sub(r'\s+', '', h.upper()) for h in expected_headers]

    columns_start_x = []
    col_index = 0
    current_norm = ""
    current_col_start = None
    debug_rows = []

    for w in header_words:
        word_text = w[4]
        word_norm = re.sub(r'\s+', '', word_text.upper())

        if col_index >= len(expected_norm):
            debug_rows.append((word_text, w[0], w[1], w[2], w[3],
                                "SOBRA (todas as colunas esperadas já fechadas)"))
            continue

        if current_col_start is None:
            current_col_start = w[0]

        current_norm += word_norm
        target = expected_norm[col_index]

        if current_norm == target:
            columns_start_x.append(current_col_start)
            debug_rows.append((word_text, w[0], w[1], w[2], w[3],
                                f"fecha coluna {col_index} = '{expected_headers[col_index]}'"))
            col_index += 1
            current_norm = ""
            current_col_start = None

        elif target.startswith(current_norm):
            debug_rows.append((word_text, w[0], w[1], w[2], w[3],
                                f"acumulando p/ coluna {col_index} ('{expected_headers[col_index]}')"))

        else:
            debug_rows.append((word_text, w[0], w[1], w[2], w[3],
                                f"⚠ MISMATCH: esperava prefixo de '{expected_headers[col_index]}', "
                                f"acumulado='{current_norm}'"))
            return None, False, debug_rows

    ok = col_index >= len(expected_norm)
    if not ok:
        faltando = expected_headers[col_index:]
        debug_rows.append(("(fim das palavras)", None, None, None, None,
                            f"⚠ Acabaram as palavras do headerArea antes de casar: {faltando}"))
        return None, False, debug_rows

    x_ranges = [area_left]
    for x0 in columns_start_x[1:]:
        x_ranges.append(x0 - 3)
    x_ranges.append(area_right)
    x_ranges = sorted(set(x_ranges))
    return x_ranges, True, debug_rows


# ---------------------------------------------------------------------------
# Método antigo (gap > 4pt), mantido aqui só para comparação lado a lado
# ---------------------------------------------------------------------------
def build_x_ranges_by_gap(header_words, area_left, area_right, gap_threshold=4):
    blocks = []
    for w in header_words:
        if not blocks or w[0] - blocks[-1][2] > gap_threshold:
            blocks.append([w[0], w[1], w[2], w[3], w[4]])
        else:
            blocks[-1][2] = w[2]
            blocks[-1][4] = blocks[-1][4] + " " + w[4]

    x_range = []
    if blocks:
        x_range.append(area_left)
        for b in blocks[1:]:
            x_range.append(b[0] - 3)
        x_range.append(area_right)
        x_range = sorted(set(x_range))
    return x_range, blocks


# ---------------------------------------------------------------------------
# ITEM 3 (v2 - CORRIGIDA): monta as linhas lógicas (1 por negociação) na mão,
# a partir das palavras cruas, usando como âncora a coluna D/C (última
# coluna). Essa coluna NUNCA quebra linha — é sempre um único caractere
# 'C' ou 'D' — então é o marcador mais confiável de "aqui existe de fato
# uma negociação".
#
# Por que não dá pra usar 'extract_table' + merge simples (v1, abaixo,
# mantida só de referência): quando a célula 'Especificação do título' tem
# mais linhas que a célula-âncora (que tem só 1 linha), o wkhtmltopdf
# CENTRALIZA verticalmente o texto da célula maior. Isso faz a 1ª linha do
# texto quebrado cair ACIMA da linha-âncora, e só a(s) linha(s) seguinte(s)
# caírem abaixo. Ou seja, a ordem física real pode ser
# "fragmento -> âncora -> fragmento", não só "âncora -> fragmento". Por
# isso a atribuição tem que ser por PROXIMIDADE VERTICAL (Y), não por
# "funde sempre com a linha anterior".
# ---------------------------------------------------------------------------
def assign_word_to_column(word, x_range):
    x0 = word[0]
    for i in range(len(x_range) - 1):
        if x_range[i] <= x0 < x_range[i + 1]:
            return i
    return len(x_range) - 2  # fallback: última coluna


def group_words_into_lines(words, y_tolerance=3):
    lines = defaultdict(list)
    for w in words:
        line_y = round(w[1] / y_tolerance) * y_tolerance
        lines[line_y].append(w)
    return [(y, sorted(ws, key=lambda w: w[0])) for y, ws in sorted(lines.items())]


def build_rows_by_anchor(words, x_range, anchor_regex=r'^[CD]$'):
    """
    Retorna: (final_rows, assignment_log)
    final_rows = lista de linhas (uma por negociação real), cada uma sendo
                 lista de strings, uma por coluna.
    assignment_log = lista de dicts descrevendo cada linha física não-âncora
                      e a qual âncora (por Y) ela foi atribuída — pra
                      diagnóstico visual.
    """
    lines = group_words_into_lines(words)
    n_cols = len(x_range) - 1
    anchor_col_index = n_cols - 1  # D/C é sempre a última coluna

    line_cols = []
    line_ys = []
    for y, ws in lines:
        cols = defaultdict(list)
        for w in ws:
            col_idx = assign_word_to_column(w, x_range)
            cols[col_idx].append(w[4])
        line_cols.append(cols)
        line_ys.append(y)

    anchor_line_indices = []
    for idx, cols in enumerate(line_cols):
        anchor_text = "".join(cols.get(anchor_col_index, [])).strip()
        if re.fullmatch(anchor_regex, anchor_text):
            anchor_line_indices.append(idx)

    if not anchor_line_indices:
        return [], [{"erro": "Nenhuma linha-âncora (D/C) encontrada — "
                              "confira anchor_regex e o índice da coluna D/C."}]

    anchor_ys = [line_ys[i] for i in anchor_line_indices]
    rows = [defaultdict(list) for _ in anchor_line_indices]
    assignment_log = []

    for line_idx, (y, cols) in enumerate(zip(line_ys, line_cols)):
        if line_idx in anchor_line_indices:
            anchor_pos = anchor_line_indices.index(line_idx)
        else:
            distances = [abs(y - ay) for ay in anchor_ys]
            anchor_pos = distances.index(min(distances))
            assignment_log.append({
                "linha_y": y,
                "texto_por_coluna": {k: " ".join(v) for k, v in cols.items()},
                "atribuida_a_ancora_na_posicao": anchor_pos,
                "atribuida_a_ancora_y": anchor_ys[anchor_pos],
                "direcao": "ACIMA da âncora" if y < anchor_ys[anchor_pos] else "ABAIXO da âncora",
            })

        for col_idx, words_list in cols.items():
            rows[anchor_pos][col_idx].append((y, " ".join(words_list)))

    final_rows = []
    for row in rows:
        row_out = []
        for col_idx in range(n_cols):
            parts = sorted(row.get(col_idx, []), key=lambda t: t[0])
            row_out.append(" ".join(p[1] for p in parts).strip())
        final_rows.append(row_out)

    return final_rows, assignment_log


def print_anchor_assignment_log(assignment_log):
    if not assignment_log:
        print("\n[ÂNCORA D/C] Nenhuma linha física precisou de reatribuição por proximidade "
              "(todas as linhas já eram âncora).")
        return
    if len(assignment_log) == 1 and "erro" in assignment_log[0]:
        print(f"\n[ÂNCORA D/C] ⚠ {assignment_log[0]['erro']}")
        return
    print(f"\n[ÂNCORA D/C] {len(assignment_log)} linha(s) física(s) sem âncora própria, "
          f"atribuída(s) por proximidade vertical:")
    for entry in assignment_log:
        print(f"  - Linha Y={entry['linha_y']:.1f}  texto={entry['texto_por_coluna']}")
        print(f"    -> atribuída à âncora #{entry['atribuida_a_ancora_na_posicao']} "
              f"(Y={entry['atribuida_a_ancora_y']:.1f}), ficava {entry['direcao']}")


# ---------------------------------------------------------------------------
# ITEM 3 v1 (OBSOLETA — mantida só de referência/comparação, NÃO USAR):
# assumia que toda linha sem âncora vem DEPOIS da negociação real, o que é
# falso quando a quebra de linha da Especificação cai ANTES da âncora
# (célula multi-linha centralizada verticalmente). Ver build_rows_by_anchor
# acima para a versão corrigida.
# ---------------------------------------------------------------------------
def merge_wrapped_rows(table, anchor_col_index=0):
    """
    Junta linhas 'fantasma' (continuação por quebra de linha) na linha
    anterior. Uma linha é considerada fantasma quando a célula da coluna
    âncora (por padrão, coluna 0 = 'Q Negociação', que sempre tem algo tipo
    '1-BOVESPA...' numa negociação real) está vazia/em branco.

    Quando isso acontece, concatena o conteúdo não-vazio de cada célula da
    linha fantasma na célula correspondente da linha anterior (separado por
    espaço), e descarta a linha fantasma.

    Retorna: (merged_table, merge_log)
    merge_log = lista de dicts descrevendo cada merge feito, para diagnóstico.
    """
    if not table:
        return table, []

    merged = [list(table[0])]
    merge_log = []

    for row_idx, row in enumerate(table[1:], start=1):
        anchor_val = (row[anchor_col_index] or "").strip() if anchor_col_index < len(row) else ""

        if anchor_val == "":
            # candidata a linha fantasma: só funde se tiver ALGUM conteúdo
            # em outra coluna (senão é só uma linha vazia de verdade, comum
            # em espaçamento entre negociações, e deve ser ignorada).
            has_content = any((c or "").strip() for c in row)
            if not has_content:
                continue

            prev = merged[-1]
            changed_cols = []
            for i, cell in enumerate(row):
                cell_val = (cell or "").strip()
                if not cell_val:
                    continue
                if i >= len(prev):
                    continue
                prev_val = (prev[i] or "").strip()
                if prev_val:
                    prev[i] = f"{prev_val} {cell_val}"
                else:
                    prev[i] = cell_val
                changed_cols.append((i, cell_val))

            merge_log.append({
                "linha_fantasma_idx": row_idx,
                "linha_fantasma_raw": row,
                "fundida_na_linha_resultado_idx": len(merged) - 1,
                "colunas_alteradas": changed_cols,
            })
        else:
            merged.append(list(row))

    return merged, merge_log


def print_merge_log(merge_log):
    if not merge_log:
        print("\n[MERGE LINHAS FANTASMA] Nenhuma linha fantasma detectada (coluna âncora sempre preenchida).")
        return
    print(f"\n[MERGE LINHAS FANTASMA] {len(merge_log)} linha(s) fantasma detectada(s) e fundida(s):")
    for entry in merge_log:
        print(f"  - Linha fantasma (posição original {entry['linha_fantasma_idx']}): {entry['linha_fantasma_raw']}")
        print(f"    -> fundida na linha de resultado #{entry['fundida_na_linha_resultado_idx']}, "
              f"colunas alteradas: {entry['colunas_alteradas']}")


# ---------------------------------------------------------------------------
# ITEM 2: modo diagnóstico — imprime palavra a palavra do headerArea
# ---------------------------------------------------------------------------
def print_header_words_debug(header_words):
    print("\n[DIAGNÓSTICO] Palavras cruas do headerArea (ordenadas por x0):")
    print(f"  {'palavra':<26}{'x0':>8}{'x1':>8}{'gap_p/_anterior':>18}")
    print("  " + "-" * 62)
    prev_x1 = None
    for w in header_words:
        gap = (w[0] - prev_x1) if prev_x1 is not None else 0.0
        marcador = "  <-- gap <= 4pt (colidiria no método antigo)" if prev_x1 is not None and gap <= 4 else ""
        print(f"  {w[4]:<26}{w[0]:>8.1f}{w[2]:>8.1f}{gap:>18.1f}{marcador}")
        prev_x1 = w[2]


def print_match_debug(debug_rows):
    print("\n[DIAGNÓSTICO] Resultado do matching por header (item 1):")
    print(f"  {'palavra':<26}{'x0':>8}{'x1':>8}   status")
    print("  " + "-" * 90)
    for text, x0, y0, x1, y1, status in debug_rows:
        x0s = f"{x0:.1f}" if x0 is not None else "-"
        x1s = f"{x1:.1f}" if x1 is not None else "-"
        print(f"  {text:<26}{x0s:>8}{x1s:>8}   {status}")


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
                print(f"\n--- OPERAÇÕES ---")
                area_tuple = coordmap['tableAreaOperacoes']

                header_words = page_pymu.get_text("words", clip=coordmap['headerArea'])
                header_words.sort(key=lambda w: w[0])

                if not header_words:
                    print("AVISO: Não foram encontradas palavras na área de cabeçalho. As colunas não puderam ser estimadas.")
                    continue

                # Item 2: diagnóstico bruto das palavras
                print_header_words_debug(header_words)

                # Item 1: método novo, por matching de texto
                x_range_new, ok_new, debug_rows = (None, False, [])
                if 'headers' in coordmap:
                    x_range_new, ok_new, debug_rows = build_x_ranges_by_header_match(
                        header_words, coordmap['headers'], area_tuple[0], area_tuple[2]
                    )
                    print_match_debug(debug_rows)
                else:
                    print("AVISO: coordmap não tem 'headers' definido, não dá pra usar o método novo.")

                # Método antigo, só para comparação visual
                x_range_old, blocks_old = build_x_ranges_by_gap(header_words, area_tuple[0], area_tuple[2])
                print("\n[MÉTODO ANTIGO - gap > 4pt] Blocos formados:")
                for b in blocks_old:
                    print(f"  '{b[4]}'  (x0={b[0]:.1f}, x1={b[2]:.1f})")
                print(f"[MÉTODO ANTIGO] x_range: {x_range_old}  ({len(x_range_old)} cortes -> {max(len(x_range_old)-1,0)} colunas)")

                if ok_new:
                    print(f"\n✅ [MÉTODO NOVO] Matching por header bateu 100% com coordmap['headers'].")
                    print(f"[MÉTODO NOVO] x_range: {x_range_new}  ({len(x_range_new)} cortes -> {max(len(x_range_new)-1,0)} colunas esperadas: {len(coordmap.get('headers', []))-1})")
                    x_range = x_range_new
                else:
                    print(f"\n⚠️  [MÉTODO NOVO] Matching por header FALHOU nesta página. "
                          f"Usando o método antigo (gap) só para não travar o teste — CONFIRA o diagnóstico acima.")
                    x_range = x_range_old

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

                print("\n--- TABELA EXTRAÍDA (pdfplumber extract_table, cru) ---")
                if table:
                    for row in table:
                        if any(row):
                            print(row)
                else:
                    print("Nenhuma tabela de operações encontrada nesta área.")

                # ITEM 3 v2: método robusto, âncora pela coluna D/C + proximidade em Y.
                # Não depende do extract_table do pdfplumber — remonta as linhas
                # direto das palavras cruas do PyMuPDF.
                area_words = page_pymu.get_text("words", clip=area_tuple)
                rows_anchor, assignment_log = build_rows_by_anchor(area_words, x_range)
                print_anchor_assignment_log(assignment_log)

                print("\n--- TABELA FINAL (método âncora D/C + proximidade Y — USE ESTA) ---")
                if rows_anchor:
                    for row in rows_anchor:
                        print(row)
                else:
                    print("Nenhuma linha-âncora encontrada — não deu pra montar a tabela por este método.")


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


