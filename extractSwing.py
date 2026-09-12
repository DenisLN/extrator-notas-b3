from collections import defaultdict
from datetime import date
from decimal import Decimal
import logging
import math
import os
import fitz as pymu
import pdfplumber as pdfpu
import xxhash
import re
from sqlmodel import Session, SQLModel, select
from db_connection import engine, get_session
from sqlalchemy.exc import IntegrityError
from schemas import operacoesSwingtrade, registroNotasSwing, registroNomeAtivos
from cleanupFunctions import cleanup_dict
from assetMapper import resolve_asset_names_with_type
from areaCodes import *

# Ativos que o usuário sempre compra isoladamente de todos os outros -- uma
# nota que contenha algum deles não deve ser salva (ver process_swing_pdf).
# O texto bruto extraído da coluna de ativo NÃO é o ticker puro: notas reais
# mostram por ex. "INVESTO LFTB F11" (o "F" de lote fracionário fica colado
# entre a raiz do ticker e o "11", às vezes com espaço em volta).
TICKERS_IGNORADOS = ('GOLD11', 'LFTB11', 'DEBB11')

# Fallback só para nomes brutos que o mapeamento em registroNomeAtivos ainda
# não conhece (ver get_nomes_brutos_ignorados_conhecidos). O antigo
# `'LFTB11' in nome_ativo.upper()` nunca batia com o formato acima.
ATIVOS_IGNORADOS_PATTERNS = [
    re.compile(re.escape(ativo[:-2]) + r'\s*F?\s*' + re.escape(ativo[-2:]))
    for ativo in TICKERS_IGNORADOS
]


def get_nomes_brutos_ignorados_conhecidos() -> set[str]:
    """Consulta registroNomeAtivos -- a mesma tabela que assetMapper.py usa
    para resolver nome bruto do PDF -> ticker canônico -- e retorna todo
    nome bruto (nomeAtivo) já mapeado para um dos tickers ignorados. É a
    fonte mais confiável, pois reflete exatamente as variações de texto já
    vistas e resolvidas (manualmente ou por heurística) no passado."""
    try:
        with get_session() as session:
            stmt = select(registroNomeAtivos.nomeAtivo).where(
                registroNomeAtivos.nomeFantasia.in_(TICKERS_IGNORADOS)
            )
            return set(session.exec(stmt).all())
    except Exception as e:
        logging.warning(f"Não foi possível consultar registroNomeAtivos para a lista de exclusão: {e}")
        return set()


def is_ativo_ignorado(nome_ativo: str, nomes_brutos_conhecidos: set[str] = frozenset()) -> bool:
    if nome_ativo in nomes_brutos_conhecidos:
        return True
    nome_upper = nome_ativo.upper()
    return any(pattern.search(nome_upper) for pattern in ATIVOS_IGNORADOS_PATTERNS)


def calcular_irrf_por_tipo(vendas_por_tipo: dict[str, Decimal]) -> dict[str, Decimal]:
    """
    Calcula o IRRF de swing trade por categoria de ativo.
    A fórmula usa TRUNCAMENTO (floor), não arredondamento.
    Verificado matematicamente em múltiplas notas reais (BTG e XP).
    """
    TAXA = Decimal('0.00005')
    resultado: dict[str, Decimal] = {}
    for tipo, total_vendas in vendas_por_tipo.items():
        irrf_bruto = total_vendas * TAXA
        irrf_truncado = Decimal(math.floor(irrf_bruto * 100)) / 100
        resultado[tipo] = irrf_truncado
    return resultado


def build_x_ranges_by_header_match(header_words, expected_headers, area_left, area_right):
    """
    Casa sequencialmente as palavras extraídas do headerArea contra a lista
    canônica expected_headers (coordmap['headers']).
    Acumula palavras até fechar exatamente o texto esperado do cabeçalho.
    
    Retorna: (x_ranges, ok_bool)
    """
    expected_norm = [re.sub(r'\s+', '', h.upper()) for h in expected_headers]

    columns_start_x = []
    col_index = 0
    current_norm = ""
    current_col_start = None

    for w in header_words:
        word_text = w[4]
        word_norm = re.sub(r'\s+', '', word_text.upper())

        if col_index >= len(expected_norm):
            continue

        if current_col_start is None:
            current_col_start = w[0]

        current_norm += word_norm
        target = expected_norm[col_index]

        if current_norm == target:
            columns_start_x.append(current_col_start)
            col_index += 1
            current_norm = ""
            current_col_start = None

        elif target.startswith(current_norm):
            continue

        else:
            return None, False

    ok = col_index >= len(expected_norm)
    if not ok:
        return None, False

    x_ranges = [area_left]
    for x0 in columns_start_x[1:]:
        x_ranges.append(x0 - 3)
    x_ranges.append(area_right)
    x_ranges = sorted(list(set(x_ranges)))
    return x_ranges, True


def build_x_ranges_by_header_first_token(header_words, expected_headers, area_left, area_right):
    """
    Casa apenas o PRIMEIRO TOKEN de cada header esperado (ex.: 'Tipo' para
    'Tipo mercado', 'Preço' para 'Preço / Ajuste') contra as palavras do
    headerArea, ORDENADAS SÓ POR X (sem tentar separar 'linha 1' de
    'linha 2' por Y).

    Por que não separar por linha: uma primeira versão desta função
    agrupava as palavras por Y (com tolerância de poucos pontos) pra
    isolar a linha 1 do cabeçalho. Isso se mostrou frágil na prática --
    fontes/kerning da XP produzem jitter vertical entre palavras da MESMA
    linha visual (ex.: por causa de negrito parcial), e esse jitter às
    vezes é maior que a tolerância usada, quebrando uma única linha visual
    em vários grupos de Y diferentes -- fazendo a palavra que a gente
    precisava (ex. 'Tipo') cair fora do grupo escolhido como 'linha 1', e
    o matching falhava por completo (caindo no fallback antigo, que é
    ainda mais frágil pra esse formato).

    A abordagem aqui é mais simples e não depende de Y: ordena TODAS as
    palavras do headerArea só por X0, e busca sequencialmente o primeiro
    token de cada header, PULANDO qualquer palavra que não bata (sejam
    fragmentos de continuação de linha 2 tipo 'mercado', 'Ajuste', '(*)',
    sejam palavras fora de ordem por causa da mistura de linhas no sort
    por X). Isso funciona com segurança porque nenhum fragmento de
    continuação de header colide textualmente com o primeiro token de
    outro header -- e valida isso com uma checagem de monotonicidade no
    final (os X0 encontrados têm que estar em ordem crescente; se não
    estiverem, algo bateu errado e a função falha em vez de devolver um
    x_range corrompido).

    Retorna: (x_ranges | None, ok: bool, motivo_falha: str | None)
    """
    if not header_words:
        return None, False, "headerArea sem nenhuma palavra"

    words_sorted = sorted(header_words, key=lambda w: w[0])
    expected_first_tokens = [re.sub(r'\s+', '', h.split()[0].upper()) for h in expected_headers]

    columns_start_x = []
    search_from = 0
    for header_full, token in zip(expected_headers, expected_first_tokens):
        found = False
        for i in range(search_from, len(words_sorted)):
            w = words_sorted[i]
            w_norm = re.sub(r'\s+', '', w[4].upper())
            if w_norm == token:
                columns_start_x.append(w[0])
                search_from = i + 1
                found = True
                break
        if not found:
            return None, False, f"não encontrei o token '{token}' (do header '{header_full}') no headerArea"

    for i in range(1, len(columns_start_x)):
        if columns_start_x[i] <= columns_start_x[i - 1]:
            return None, False, (f"X0 fora de ordem crescente (header '{expected_headers[i]}' "
                                  f"em x0={columns_start_x[i]:.1f} <= header anterior "
                                  f"'{expected_headers[i-1]}' em x0={columns_start_x[i-1]:.1f})")

    x_ranges = [area_left]
    for x0 in columns_start_x[1:]:
        x_ranges.append(x0 - 3)
    x_ranges.append(area_right)
    x_ranges = sorted(set(x_ranges))
    return x_ranges, True, None


def build_header_index_by_key(coordmap):
    """
    Mapeia cada chave lógica usada no pipeline (coordmap['columns'], ex.:
    'operacaoTipo', 'nomeAtivo', 'quantidade'...) para o ÍNDICE de coluna
    correspondente em coordmap['headers'], por correspondência de texto
    normalizado.

    Faz 2 passadas:
    1) MATCH EXATO em todos os headers primeiro. Só depois de checar
       TODOS os headers por igualdade exata é que se considera substring.
    2) Fallback por substring, só se não achou exato -- e só considerando
       headers com >=3 caracteres normalizados, pra headers curtos (ex.:
       'Q') não colarem por acidente dentro de outro header mais longo
       que o contém como substring (ex.: 'Q' dentro de 'Quantidade' --
       bug real encontrado: sem essa guarda, 'quantidade' era mapeado pra
       coluna 'Q' em vez de 'Quantidade', e a quantidade lida sempre dava
       0, silenciosamente).

    Só é seguro de usar quando os x_ranges da página foram calculados via
    build_x_ranges_by_header_first_token com sucesso E o nº de colunas
    resultante bate exatamente com len(coordmap['headers']) -- porque aí,
    e só aí, coluna i == headers[i], garantido.
    """
    headers_norm = [re.sub(r'\s+', '', h.upper()) for h in coordmap.get('headers', [])]
    index_by_key = {}
    for key, expected_header in coordmap.get('columns', {}).items():
        exp_norm = re.sub(r'\s+', '', expected_header.upper())

        match_idx = None
        for idx, h_norm in enumerate(headers_norm):
            if h_norm == exp_norm:
                match_idx = idx
                break

        if match_idx is None:
            for idx, h_norm in enumerate(headers_norm):
                if len(h_norm) >= 3 and (exp_norm in h_norm or h_norm in exp_norm):
                    match_idx = idx
                    break

        if match_idx is not None:
            index_by_key[key] = match_idx

    return index_by_key


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


def build_rows_by_anchor(words, x_range, anchor_regex=r'^[CD]$', max_wrap_distance_fallback=12.0):
    """
    Reconstrói as linhas lógicas da tabela de operações (1 linha por
    negociação) diretamente das palavras cruas -- SEM depender do
    extract_table do pdfplumber, que quebra quando a Especificação do
    título ocupa mais de 1 linha física.

    Âncora = coluna D/C (sempre a última), que NUNCA quebra linha (é
    sempre 1 único caractere 'C' ou 'D'). Toda linha física que não é
    âncora é candidata a fragmento de quebra de linha (normalmente da
    Especificação do título) e é atribuída à âncora MAIS PRÓXIMA EM Y --
    podendo estar ACIMA ou ABAIXO dela, porque o wkhtmltopdf centraliza
    verticalmente células multi-linha em relação a células de 1 linha
    (ou seja, a 1ª linha do texto quebrado pode cair ANTES da
    linha-âncora).

    FILTRO DE DISTÂNCIA: nem todo fragmento "órfão" é uma quebra de linha
    legítima -- às vezes é ruído de cabeçalho vazando pra dentro da área
    de dados (headerArea/tableAreaOperacoes calibrados pra cabeçalho de 1
    linha, agora que o cabeçalho tem 2+ linhas). Uma quebra de linha
    legítima fica a poucos pontos da âncora (a própria altura de uma
    linha de texto); ruído externo fica a uma linha inteira de distância
    ou mais. Calcula o espaçamento típico entre âncoras reais e descarta
    (não funde em NENHUMA linha) qualquer fragmento cuja âncora mais
    próxima esteja além de metade desse espaçamento.

    Retorna: (final_rows, assignment_log, discarded_log)
    final_rows = lista de linhas, cada uma lista de strings por coluna.
    assignment_log = fragmentos fundidos com sucesso (diagnóstico).
    discarded_log = fragmentos descartados por estarem longe demais de
                     qualquer âncora (provável ruído/vazamento de
                     cabeçalho) -- vale a pena olhar se aparecer algo
                     inesperado aqui.
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
        return [], [], [{"erro": "Nenhuma linha-âncora (D/C) encontrada nesta área/página."}]

    anchor_ys = [line_ys[i] for i in anchor_line_indices]

    if len(anchor_ys) >= 2:
        sorted_ys = sorted(anchor_ys)
        diffs = sorted(d for d in (sorted_ys[i + 1] - sorted_ys[i] for i in range(len(sorted_ys) - 1)) if d > 0)
        typical_spacing = diffs[len(diffs) // 2] if diffs else max_wrap_distance_fallback * 2
        max_valid_distance = typical_spacing / 2
    else:
        max_valid_distance = max_wrap_distance_fallback

    rows = [defaultdict(list) for _ in anchor_line_indices]
    assignment_log = []
    discarded_log = []

    for line_idx, (y, cols) in enumerate(zip(line_ys, line_cols)):
        if line_idx in anchor_line_indices:
            anchor_pos = anchor_line_indices.index(line_idx)
        else:
            distances = [abs(y - ay) for ay in anchor_ys]
            min_dist = min(distances)
            anchor_pos = distances.index(min_dist)

            if min_dist > max_valid_distance:
                discarded_log.append({
                    "linha_y": y,
                    "texto_por_coluna": {k: " ".join(v) for k, v in cols.items()},
                    "distancia_da_ancora_mais_proxima": min_dist,
                    "limite_valido": max_valid_distance,
                })
                continue  # ruído -- não funde em nenhuma linha

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

    return final_rows, assignment_log, discarded_log


logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("pdfminer").setLevel(logging.WARNING)

SQLModel.metadata.create_all(engine)


def process_swing_pdf(pdf_path: str, external_session: Session = None, dry_run: bool = False) -> bool:
    with open(pdf_path, "rb") as f:
        file_hash = xxhash.xxh64(f.read()).hexdigest()

    logging.info(f"Processando Swing Trade PDF: {pdf_path} (Hash: {file_hash})")

    nomes_brutos_ignorados_conhecidos = get_nomes_brutos_ignorados_conhecidos()

    doc_pymu = pymu.open(pdf_path)
    start_page = doc_pymu[0]

    # Heurística para identificar a corretora
    broker = ""
    coordmap = {}

    for entry, rect in brokerName_swing.items():
        if rect.get_area() > 0:
            bloco = start_page.get_textbox(rect)
            if "XP" in bloco:
                coordmap = areaDict_xp_swing
                broker = "XP"
                break
            elif "BTG" in bloco:
                coordmap = areaDict_btg_swing
                broker = "BTG"
                break

    if not coordmap:
        coordmap = areaDict_xp_swing
        broker = "XP"

    logging.info(f"Corretora identificada: {broker}")

    # Mapeamento estático coluna->campo, calculado 1x (não muda por página).
    # Só é usado quando o matching de header por página confirma que o nº
    # de colunas bate exatamente com len(coordmap['headers']) -- ver
    # 'usar_metodo_robusto' dentro do loop de páginas.
    header_index_by_key = build_header_index_by_key(coordmap)
    logging.debug(f"Mapeamento estático coluna->campo (via coordmap['headers']/['columns']): {header_index_by_key}")

    settings = { 
        "vertical_strategy": "explicit",
        "horizontal_strategy": "text",
        "intersection_x_tolerance": 20,
        "intersection_y_tolerance": 20,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 0,
    }

    aggregated_trades = {}
    ativos_negociados = {}
    nota_tem_ativo_ignorado = False
    last_header = {}
    summary_data = {
        'taxas': Decimal('0.00'),
        'irrf': Decimal('0.00'),
        'liquidoCalc': Decimal('0.00'),
        'liquidoReal': Decimal('0.00'),
    }

    dynamic_cols = {}
    with pdfpu.open(pdf_path) as pdf_doc:
        for page_idx, (page_pymu, page_pdfpu) in enumerate(zip(doc_pymu, pdf_doc.pages), start=1):
            # Extração dos dados do cabeçalho
            nr_nota = cleanup_dict['nrNota'](page_pymu.get_textbox(coordmap['nrNota']).strip())
            data_val = cleanup_dict['data'](page_pymu.get_textbox(coordmap['data']).strip())
            cpf_val = cleanup_dict['cpf'](page_pymu.get_textbox(coordmap['cpf']).strip())
            n_cliente_val = cleanup_dict['nCliente'](page_pymu.get_textbox(coordmap['nCliente']).strip())

            last_header = {
                'nrNota': nr_nota,
                'data': data_val,
                'cpf': cpf_val,
                'nCliente': n_cliente_val,
            }
            if page_idx == 1 and (not data_val or not n_cliente_val or not nr_nota):
                logging.warning(f"⚠️  Falha ao extrair dados fundamentais no Swing Trade: {pdf_path}")
                with open("arquivos_sem_texto.txt", "a", encoding="utf-8") as f_out:
                    f_out.write(pdf_path + "\n")
                return False
                
            logging.debug(f"Página {page_idx}: Headers extraídos -> nrNota={nr_nota}, data={data_val}, cpf={cpf_val}, nCliente={n_cliente_val}")

            # Extração das operações
            table_area = coordmap['tableAreaOperacoes']

            # Recorte ajustado: começa no maior valor entre o topo de
            # tableAreaOperacoes e o fundo do headerArea. Evita que texto
            # do cabeçalho vaze pra dentro da área de dados (o que
            # poluiria a 1ª negociação real no método por âncora, já que
            # ela não teria nenhuma âncora "acima" pra brecar a atribuição
            # por proximidade em Y).
            if 'headerArea' in coordmap:
                adjusted_top = max(table_area[1], coordmap['headerArea'][3])
            else:
                adjusted_top = table_area[1]
            table_area_clip = pymu.Rect(table_area[0], adjusted_top, table_area[2], table_area[3])

            cropped = page_pdfpu.crop(table_area)

            x_ranges = []
            ok_header_match = False
            # Tenta primeiro o matching por 1º token do header (robusto a
            # cabeçalho quebrado em 2+ linhas).
            if 'headerArea' in coordmap and 'headers' in coordmap:
                header_words = page_pymu.get_text("words", clip=coordmap['headerArea'])
                if header_words:
                    x_ranges_new, ok_header_match, falha_motivo = build_x_ranges_by_header_first_token(
                        header_words,
                        coordmap['headers'],
                        table_area[0],
                        table_area[2]
                    )
                    if ok_header_match:
                        x_ranges = x_ranges_new
                        logging.debug(f"Página {page_idx}: x_ranges calculados com sucesso via matching por header (1º token).")
                    else:
                        logging.warning(f"Página {page_idx}: matching por header (1º token) falhou: {falha_motivo}")

            # Só confiamos no mapeamento estático coluna->campo (e portanto
            # no método robusto por âncora) quando o nº de colunas bate
            # EXATAMENTE com len(coordmap['headers']) -- garantindo
            # coluna[i] == headers[i].
            usar_metodo_robusto = ok_header_match and (len(x_ranges) - 1 == len(coordmap['headers']))

            if usar_metodo_robusto:
                area_words = page_pymu.get_text("words", clip=table_area_clip)
                rows_final, assignment_log, discarded_log = build_rows_by_anchor(area_words, x_ranges)

                if discarded_log and any('erro' in entry for entry in discarded_log):
                    logging.warning(f"Página {page_idx}: método âncora D/C não encontrou nenhuma "
                                     f"linha-âncora ({discarded_log[0].get('erro')}). Nenhuma operação lida nesta página.")
                else:
                    if assignment_log:
                        logging.debug(f"Página {page_idx}: {len(assignment_log)} linha(s) de continuação "
                                       f"(quebra de linha) reatribuída(s) por proximidade vertical em Y.")
                    if discarded_log:
                        logging.warning(f"Página {page_idx}: {len(discarded_log)} linha(s) física(s) descartada(s) "
                                         f"por estarem longe demais de qualquer âncora (provável ruído/vazamento de "
                                         f"cabeçalho) -- CONFIRA se não é conteúdo real perdido: {discarded_log}")

                for row in rows_final:
                    if not any(row):
                        continue

                    required_keys = ['operacaoTipo', 'nomeAtivo', 'quantidade', 'precoAjuste', 'precoOperacao']
                    if any(k not in header_index_by_key for k in required_keys):
                        logging.error(f"🚨 ERRO CRÍTICO: Mapeamento de colunas incompleto em {pdf_path}. header_index_by_key={header_index_by_key}")
                        with open("notas_colunas_nao_mapeadas.txt", "a", encoding="utf-8") as f_out:
                            f_out.write(f"{pdf_path} | header_index_by_key={header_index_by_key}\n")
                        return False

                    try:
                        op_tipo = cleanup_dict['operacaoTipo'](row[header_index_by_key['operacaoTipo']])
                        nome_ativo = cleanup_dict['nomeAtivo'](row[header_index_by_key['nomeAtivo']])
                        qnt = cleanup_dict['quantidade'](row[header_index_by_key['quantidade']])
                        preco_op = cleanup_dict['precoOperacao'](row[header_index_by_key['precoOperacao']])
                        obs_val = row[header_index_by_key['obs']] if 'obs' in header_index_by_key else ''
                        # 'D' = Day Trade
                        is_day = 'D' in str(obs_val or '')
                    except (IndexError, KeyError) as e:
                        logging.warning(f"Erro ao acessar colunas na linha {row}: {e}")
                        continue

                    if is_ativo_ignorado(nome_ativo, nomes_brutos_ignorados_conhecidos):
                        logging.info(f"Ativo {nome_ativo} ignorado conforme regra.")
                        nota_tem_ativo_ignorado = True
                        continue

                    logging.debug(f"Página {page_idx}: Linha lida -> row={row}")
                    logging.debug(f"Página {page_idx}: Dados parseados -> op_tipo={op_tipo}, ativo={nome_ativo}, qnt={qnt}, preco={preco_op}, is_day={is_day}")

                    key = (nr_nota, nome_ativo, op_tipo, is_day)
                    if key not in aggregated_trades:
                        aggregated_trades[key] = {
                            'data': data_val,
                            'operacaoTipo': op_tipo,
                            'nomeAtivo': nome_ativo,
                            'quantidade': 0,
                            'totalValor': Decimal('0.00'),
                            'cpf': cpf_val,
                            'nCliente': n_cliente_val,
                            'corretora': broker,
                            'nrNota': nr_nota,
                            'hashArquivo': file_hash,
                            'isDayTrade': is_day,
                        }

                    aggregated_trades[key]['quantidade'] += qnt
                    aggregated_trades[key]['totalValor'] += preco_op

                    ativos_negociados[nome_ativo] = op_tipo

            else:
                # FALLBACK (método antigo: gap + extract_table). Usado só
                # quando o matching de header falha totalmente (formato de
                # cabeçalho fora do esperado). ATENÇÃO: este caminho NÃO
                # tem a correção de quebra de linha na Especificação do
                # título -- sujeito ao bug antigo se a nota tiver esse
                # tipo de quebra.
                logging.warning(f"Página {page_idx}: matching de header falhou -- usando fallback antigo "
                                 f"(gap + extract_table), SEM correção de quebra de linha. Confira esta nota manualmente.")

                if 'headerArea' in coordmap:
                    header_words = page_pymu.get_text("words", clip=coordmap['headerArea'])
                    header_words.sort(key=lambda w: w[0])

                    blocks = []
                    for w in header_words:
                        if not blocks or w[0] - blocks[-1][2] > 4:
                            blocks.append(w)
                        else:
                            blocks[-1] = (blocks[-1][0], blocks[-1][1], w[2], w[3], blocks[-1][4] + " " + w[4])

                    if blocks:
                        x_ranges = [table_area[0]]
                        for b in blocks[1:]:
                            x_ranges.append(b[0] - 3)
                        x_ranges.append(table_area[2])
                        x_ranges = sorted(list(set(x_ranges)))
                else:
                    x_ranges = coordmap.get('xRangeOperacoes', [])

                page_settings = settings.copy()
                if x_ranges:
                    page_settings["explicit_vertical_lines"] = x_ranges

                table = cropped.extract_table(page_settings)

                if table:
                    for row in table:
                        row_norm = [re.sub(r'\s+', '', c.upper()) if c else '' for c in row]
                        if any('QUANTIDADE' in c or 'C/V' in c or 'NEGOCIACAO' in c or 'NEGOCIAÇÃO' in c for c in row_norm):
                            for key_col, expected_header in coordmap['columns'].items():
                                exp_norm = re.sub(r'\s+', '', expected_header.upper())
                                for idx, col_name in enumerate(row):
                                    if col_name and isinstance(col_name, str):
                                        col_norm = re.sub(r'\s+', '', col_name.upper())
                                        if exp_norm in col_norm:
                                            dynamic_cols[key_col] = idx
                                            break
                            logging.debug(f"Página {page_idx}: Colunas mapeadas dinamicamente (fallback): {dynamic_cols}")
                            continue

                        if not any(row) or 'Prazo' in row or row[0] == coordmap['headers'][0]:
                            continue

                        required_keys = ['operacaoTipo', 'nomeAtivo', 'quantidade', 'precoAjuste', 'precoOperacao']
                        if any(k not in dynamic_cols for k in required_keys):
                            logging.error(f"🚨 ERRO CRÍTICO: Mapeamento de colunas incompleto em {pdf_path}. dynamic_cols={dynamic_cols}")
                            with open("notas_colunas_nao_mapeadas.txt", "a", encoding="utf-8") as f_out:
                                f_out.write(f"{pdf_path} | dynamic_cols={dynamic_cols}\n")
                            return False

                        try:
                            op_tipo = cleanup_dict['operacaoTipo'](row[dynamic_cols['operacaoTipo']])
                            nome_ativo = cleanup_dict['nomeAtivo'](row[dynamic_cols['nomeAtivo']])
                            qnt = cleanup_dict['quantidade'](row[dynamic_cols['quantidade']])
                            preco_op = cleanup_dict['precoOperacao'](row[dynamic_cols['precoOperacao']])
                            obs_val = row[dynamic_cols['obs']] if 'obs' in dynamic_cols and dynamic_cols['obs'] < len(row) else ''
                            is_day = 'D' in str(obs_val or '')
                        except (IndexError, KeyError) as e:
                            logging.warning(f"Erro ao acessar colunas na linha {row}: {e}")
                            continue

                        if is_ativo_ignorado(nome_ativo, nomes_brutos_ignorados_conhecidos):
                            logging.info(f"Ativo {nome_ativo} ignorado conforme regra.")
                            nota_tem_ativo_ignorado = True
                            continue

                        logging.debug(f"Página {page_idx}: Linha lida -> row={row}")
                        logging.debug(f"Página {page_idx}: Dados parseados -> op_tipo={op_tipo}, ativo={nome_ativo}, qnt={qnt}, preco={preco_op}, is_day={is_day}")

                        key = (nr_nota, nome_ativo, op_tipo, is_day)
                        if key not in aggregated_trades:
                            aggregated_trades[key] = {
                                'data': data_val,
                                'operacaoTipo': op_tipo,
                                'nomeAtivo': nome_ativo,
                                'quantidade': 0,
                                'totalValor': Decimal('0.00'),
                                'cpf': cpf_val,
                                'nCliente': n_cliente_val,
                                'corretora': broker,
                                'nrNota': nr_nota,
                                'hashArquivo': file_hash,
                                'isDayTrade': is_day,
                            }

                        aggregated_trades[key]['quantidade'] += qnt
                        aggregated_trades[key]['totalValor'] += preco_op

                        ativos_negociados[nome_ativo] = op_tipo

            # Extração dos resumos na última página da nota
            summary_text = ""
            for key_area in ['tableClearing', 'tableBolsa', 'tableCustos']:
                rect = coordmap.get(key_area)
            if broker == "BTG" and 'tableCustos' in coordmap:
                has_continua = "CONTINUA" in page_pymu.get_textbox(coordmap['tableCustos']).upper()
            else:
                has_continua = "CONTINUA" in page_pymu.get_text().upper()
            logging.debug(f"Página {page_idx}: has_continua={has_continua}, summary_text_length={len(summary_text)}")

            if not has_continua:
                total_taxas = Decimal('0.00')
                total_irrf = Decimal('0.00')
                total_irrf_deduzido = Decimal('0.00')
                liquido_real_val = Decimal('0.00')

                for key_area in ['tableCustos', 'tableClearing', 'tableBolsa']:
                    rect = coordmap.get(key_area)
                    if not rect:
                        continue
                    words = page_pymu.get_text("words", clip=rect)
                    words_sorted_by_y = sorted(words, key=lambda w: w[1])
                    lines = []
                    for w in words_sorted_by_y:
                        if not lines or abs(w[1] - lines[-1][0][1]) > 4:
                            lines.append([w])
                        else:
                            lines[-1].append(w)

                    for line_words in lines:
                        words_sorted = sorted(line_words, key=lambda w: w[0])
                        line_text = " ".join(w[4] for w in words_sorted)
                        clean_value_text = line_text.replace(" D", "").replace(" C", "").strip()

                        if "I.R.R.F." in line_text:
                            val_irrf = cleanup_dict['precoOperacao'](clean_value_text)
                            total_irrf += val_irrf
                            has_d = line_text.strip().endswith(" D")
                            if has_d:
                                total_irrf_deduzido += val_irrf
                            logging.debug(f"  [TAXA/{key_area}] IRRF -> val={val_irrf} | has_D={has_d} | irrf_deduzido={total_irrf_deduzido} | linha='{line_text}'")
                        elif "Líquido para" in line_text:
                            val_liq = cleanup_dict['precoOperacao'](clean_value_text)
                            liquido_real_val = -abs(val_liq) if line_text.strip().endswith(" D") else abs(val_liq)
                            logging.debug(f"  [TAXA/{key_area}] LiquidoReal -> val={liquido_real_val} | linha='{line_text}'")
                        elif line_text.strip().endswith(" D") and not line_text.startswith("Total") and not line_text.startswith("Líquido") and not line_text.startswith("Valor líquido") and not line_text.startswith("Soma"):
                            added = cleanup_dict['precoOperacao'](clean_value_text)
                            total_taxas += added
                            logging.debug(f"  [TAXA/{key_area}] TAXA +{added} -> total_taxas={total_taxas} | linha='{line_text}'")

                # Leitura e verificação do Resumo dos Negócios
                vendas_vista = Decimal('0.00')
                compras_vista = Decimal('0.00')

                if 'tableAreaResumo' in coordmap:
                    rect_resumo = coordmap['tableAreaResumo']
                    words_resumo = page_pymu.get_text("words", clip=rect_resumo)
                    words_sorted_by_y = sorted(words_resumo, key=lambda w: w[1])
                    lines_resumo = []
                    for w in words_sorted_by_y:
                        if not lines_resumo or abs(w[1] - lines_resumo[-1][0][1]) > 4:
                            lines_resumo.append([w])
                        else:
                            lines_resumo[-1].append(w)

                    for line_words in lines_resumo:
                        words_sorted = sorted(line_words, key=lambda w: w[0])
                        line_text = " ".join(w[4] for w in words_sorted)
                        clean_value_text = line_text.replace(" D", "").replace(" C", "").strip()

                        if "Vendas à vista" in line_text:
                            vendas_vista = cleanup_dict['precoOperacao'](clean_value_text)
                        elif "Compras à vista" in line_text:
                            compras_vista = cleanup_dict['precoOperacao'](clean_value_text)

                liquido_conferido = vendas_vista - compras_vista - total_taxas - total_irrf_deduzido
                
                logging.debug(f"Página {page_idx}: Resumo Extraído -> vendas_vista={vendas_vista}, compras_vista={compras_vista}, total_taxas={total_taxas}, liquido_calc={liquido_conferido}, liquido_real={liquido_real_val}, total_irrf={total_irrf}")

                summary_data['taxas'] = total_taxas
                summary_data['irrf'] = total_irrf
                summary_data['liquidoReal'] = liquido_real_val
                summary_data['liquidoCalc'] = liquido_conferido
                
                # Extração do irrfDay
                summary_data['irrfDay'] = None
                rect_irrf_day = coordmap.get('irrf_day')
                if rect_irrf_day:
                    txt_irrf_day = page_pymu.get_textbox(rect_irrf_day)
                    if txt_irrf_day:
                        val_irrf_day = cleanup_dict['irrfDay'](txt_irrf_day)
                        if val_irrf_day > 0:
                            summary_data['irrfDay'] = val_irrf_day
                            logging.debug(f"  [irrf_day] IRRF Day Trade Encontrado: {val_irrf_day}")

                if total_taxas == Decimal('0.00'):
                    logging.warning(f"⚠️ Nota sem custo detectada (taxas=0). Abortando processamento. {pdf_path}")
                    with open("notas_sem_custo.txt", "a", encoding="utf-8") as f_out:
                        f_out.write(f"{pdf_path}\n")
                    return False
                
                has_venda = any(t['operacaoTipo'] == 'V' for t in aggregated_trades.values())
                if has_venda and total_irrf == Decimal('0.00'):
                    logging.info(f"ℹ️ Nota com venda e sem IRRF debitado na nota. {pdf_path}")

    if nota_tem_ativo_ignorado:
        if aggregated_trades:
            logging.error(
                f"🚨 ERRO CRÍTICO: Nota mistura ativo(s) da lista de exclusão (GOLD11/LFTB11/DEBB11) com "
                f"outro(s) ativo(s) normal(is) -- comportamento inesperado, pois esses ativos deveriam ser "
                f"comprados isoladamente. Abortando! {pdf_path}"
            )
            with open("notas_ativo_ignorado_misto.txt", "a", encoding="utf-8") as f_out:
                f_out.write(f"{pdf_path}\n")
            return False

        logging.info(f"Nota contém apenas ativo(s) da lista de exclusão (GOLD11/LFTB11/DEBB11); ignorando nota inteira, nada será salvo. {pdf_path}")
        if dry_run:
            logging.info(f"🧪 [DRY-RUN] Arquivo isolado NÃO removido (simulação): {pdf_path}")
        else:
            try:
                doc_pymu.close()
                os.remove(pdf_path)
                logging.info(f"Arquivo isolado removido (nota apenas com ativo(s) ignorado(s)): {pdf_path}")
            except Exception as e:
                logging.error(f"Erro ao remover arquivo isolado ignorado {pdf_path}: {e}")
        return True

    # Validação do Lock
    if summary_data['taxas'] == Decimal('0.00') and summary_data['liquidoReal'] == Decimal('0.00') and summary_data['liquidoCalc'] == Decimal('0.00'):
        logging.error(f"🚨 ERRO CRÍTICO: Nota com valores de resumo todos zerados. Abortando! {pdf_path}")
        with open("notas_com_erro_resumo.txt", "a", encoding="utf-8") as f_out:
            f_out.write(f"{pdf_path}\n")
        return False

    def execute_persistence(session: Session) -> bool:
        unique_assets = list(ativos_negociados.keys())
        mapped_names = resolve_asset_names_with_type(session, unique_assets)

        if mapped_names is None:
            logging.error("Execução pausada. Preencha o arquivo 'missing_assets.yaml' com os tickers correspondentes e execute novamente.")
            return False

        # Acumular vendas por tipo de ativo para cálculo de IRRF breakdown
        vendas_por_tipo: dict[str, Decimal] = defaultdict(Decimal)
        for trade in aggregated_trades.values():
            if trade['operacaoTipo'] == 'V' and not trade.get('isDayTrade', False):
                tipo = mapped_names.get(trade['nomeAtivo'], {}).get('tipoAtivo', 'ACAO')
                vendas_por_tipo[tipo] += trade['totalValor']

        irrf_por_tipo = calcular_irrf_por_tipo(vendas_por_tipo)

        # Failsafe de IRRF
        irrf_calculado = sum(irrf_por_tipo.values())
        irrf_reportado = summary_data['irrf']

        TOLERANCIA = Decimal('0.01')

        if irrf_reportado > Decimal('0.00') and abs(irrf_calculado - irrf_reportado) > TOLERANCIA:
            logging.error(
                f"🚨 FAILSAFE IRRF: IRRF calculated ({irrf_calculado}) difere do reportado na nota ({irrf_reportado}). "
                f"Diferença: {abs(irrf_calculado - irrf_reportado)}. Abortando nota {last_header.get('nrNota')}. "
                f"Arquivo: {pdf_path}"
            )
            with open("notas_irrf_divergente.txt", "a", encoding="utf-8") as f_out:
                f_out.write(
                    f"{pdf_path} | nota={last_header.get('nrNota')} | "
                    f"calculado={irrf_calculado} | reportado={irrf_reportado}\n"
                )
            return False

        if dry_run:
            logging.info(f"🧪 [DRY-RUN] Nota nº {last_header.get('nrNota')} simulada com sucesso (nenhuma alteração persistida no banco).")
            session.rollback()
            return True

        rel_path = str(pdf_path).replace("z:\\", "").replace("Z:\\", "").replace("z:/", "").replace("Z:/", "").replace("/mnt/", "")
        rel_path = rel_path.replace("Projetos\\notascorretagem\\", "").replace("Projetos/notascorretagem/", "")

        nota_reg = registroNotasSwing(
            data=last_header.get('data', date.today()),
            nrNota=last_header.get('nrNota', 0),
            corretora=broker,
            cpf=last_header.get('cpf', ''),
            nCliente=last_header.get('nCliente', ''),
            hashNota=file_hash,
            taxas=summary_data['taxas'],
            irrf=summary_data['irrf'],
            irrfTotal=sum(irrf_por_tipo.values()) if irrf_por_tipo else summary_data['irrf'],
            irrfAcao=irrf_por_tipo.get("ACAO"),
            irrfFii=irrf_por_tipo.get("FII"),
            irrfEtf=irrf_por_tipo.get("ETF"),
            irrfBdr=irrf_por_tipo.get("BDR"),
            irrfDay=summary_data.get('irrfDay'),
            liquidoCalc=summary_data['liquidoCalc'],
            liquidoReal=summary_data['liquidoReal'],
            ativosNegociados={mapped_names.get(k, {}).get('nomeFantasia', k): v for k, v in ativos_negociados.items()},
            relativePath=rel_path,
        )
        try:
            session.add(nota_reg)
            session.commit()
            logging.info(f"Nota nº {last_header.get('nrNota')} gravada em registroNotasSwing.")
        except IntegrityError:
            session.rollback()
            logging.error("Nota duplicada em registroNotasSwing.")
        except Exception as e:
            session.rollback()
            logging.error(f"Erro ao salvar nota em registroNotasSwing: {e}")

        vol_total_nota = sum(t['totalValor'] for t in aggregated_trades.values())

        for key, trade in aggregated_trades.items():
            qnt = trade['quantidade']
            total_val = trade['totalValor']
            preco_ajuste = Decimal(round(total_val / qnt, 2)) if qnt > 0 else Decimal('0.00')
            nome_fantasia = mapped_names.get(trade['nomeAtivo'], {}).get('nomeFantasia', trade['nomeAtivo'])

            if vol_total_nota > Decimal('0'):
                taxa_rateada = (trade['totalValor'] / vol_total_nota) * summary_data['taxas']
            else:
                taxa_rateada = Decimal('0')

            if trade['nrNota']:
                stmt = select(registroNotasSwing).where(registroNotasSwing.nrNota == trade['nrNota'])
                existing_nota = session.exec(stmt).first()
                if existing_nota and existing_nota.hashNota != file_hash:
                    logging.warning(f"Atenção: Mesma nota ({trade['nrNota']}) processada em arquivo diferente!")

            op = operacoesSwingtrade(
                data=trade['data'],
                operacaoTipo=trade['operacaoTipo'],
                nomeAtivo=nome_fantasia,
                quantidade=qnt,
                precoAjuste=preco_ajuste,
                precoOperacao=total_val,
                cpf=trade['cpf'],
                nCliente=trade['nCliente'],
                corretora=trade['corretora'],
                nrNota=trade['nrNota'],
                isDayTrade=trade['isDayTrade'],
                taxaRateada=taxa_rateada.quantize(Decimal('0.0001')),
            )
            try:
                session.add(op)
                session.commit()
            except IntegrityError:
                session.rollback()
                logging.error("Operação duplicada (UniqueConstraint violada)")
            except Exception as e:
                session.rollback()
                logging.error(f"Erro ao salvar operação: {e}")

        return True

    try:
        if external_session:
            return execute_persistence(external_session)
        else:
            with get_session() as session:
                return execute_persistence(session)
    finally:
        doc_pymu.close()


if __name__ == "__main__":
    import sys
    target_pdf = sys.argv[1] if len(sys.argv) > 1 else "nota_xp.pdf"
    process_swing_pdf(target_pdf)


