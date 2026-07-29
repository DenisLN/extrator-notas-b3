from collections import defaultdict
from datetime import date
from decimal import Decimal
import logging
import fitz as pymu
import pdfplumber as pdfpu
import xxhash
import re
from sqlmodel import Session, SQLModel, select
from db_connection import engine, get_session
from sqlalchemy.exc import IntegrityError
from schemas import operacoesSwingtrade, registroNotasSwing
from cleanupFunctions import cleanup_dict
from assetMapper import resolve_asset_names
from areaCodes import * 

logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("pdfminer").setLevel(logging.WARNING)

SQLModel.metadata.create_all(engine)

def process_swing_pdf(pdf_path: str, external_session: Session = None) -> bool:
    with open(pdf_path, "rb") as f:
        file_hash = xxhash.xxh64(f.read()).hexdigest()

    logging.info(f"Processando Swing Trade PDF: {pdf_path} (Hash: {file_hash})")

    doc_pymu = pymu.open(pdf_path)
    doc_len = len(doc_pymu)
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
            cropped = page_pdfpu.crop(coordmap['tableAreaOperacoes'])
            
            x_ranges = []
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
                    x_ranges.append(coordmap['tableAreaOperacoes'][0])
                    for b in blocks[1:]:
                        x_ranges.append(b[0] - 3)
                    x_ranges.append(coordmap['tableAreaOperacoes'][2])
                    x_ranges = sorted(list(set(x_ranges)))
            else:
                x_ranges = coordmap.get('xRangeOperacoes', [])
                
            page_settings = settings.copy()
            if x_ranges:
                page_settings["explicit_vertical_lines"] = x_ranges

            table = cropped.extract_table(page_settings)

            if table:
                for row in table: 
                    # Se for linha de cabeçalho, usamos a nova lógica elegante do usuário
                    if 'Quantidade' in row or 'C/V' in row or 'Negociação' in row:
                        for key, expected_header in coordmap['columns'].items():
                            exp_norm = re.sub(r'\s+', '', expected_header.upper())
                            for idx, col_name in enumerate(row):
                                if col_name and isinstance(col_name, str):
                                    col_norm = re.sub(r'\s+', '', col_name.upper())
                                    if exp_norm in col_norm:
                                        dynamic_cols[key] = idx
                                        break
                        logging.debug(f"Página {page_idx}: Colunas mapeadas dinamicamente: {dynamic_cols}")
                        continue
                    
                    if not any(row) or 'Prazo' in row or row[0] == coordmap['headers'][0]:
                        continue
                        
                    # Verifica se o mapeamento dinâmico obteve as colunas necessárias
                    required_keys = ['operacaoTipo', 'nomeAtivo', 'quantidade', 'precoAjuste', 'precoOperacao']
                    if any(k not in dynamic_cols for k in required_keys):
                        # Fallback seguro baseado nas imagens, para caso a primeira página cortou o cabeçalho
                        logging.warning(f"Usando fallback de colunas pois faltam chaves: {dynamic_cols}")
                        dynamic_cols = {'operacaoTipo': 2, 'nomeAtivo': 5, 'quantidade': 7, 'precoAjuste': 8, 'precoOperacao': 9}

                    try:
                        op_tipo = cleanup_dict['operacaoTipo'](row[dynamic_cols['operacaoTipo']])
                        nome_ativo = cleanup_dict['nomeAtivo'](row[dynamic_cols['nomeAtivo']])
                        qnt = cleanup_dict['quantidade'](row[dynamic_cols['quantidade']])
                        preco_op = cleanup_dict['precoOperacao'](row[dynamic_cols['precoOperacao']])
                    except IndexError as e:
                        logging.warning(f"Erro ao acessar colunas na linha {row}: {e}")
                        continue
                        
                    ativos_ignorados = ['GOLD11', 'LFTB11', 'DEBB11']
                    if any(ignorado in nome_ativo.upper() for ignorado in ativos_ignorados):
                        logging.info(f"Ativo {nome_ativo} ignorado conforme regra.")
                        continue
                        
                    logging.debug(f"Página {page_idx}: Linha lida -> row={row}")
                    logging.debug(f"Página {page_idx}: Dados parseados -> op_tipo={op_tipo}, ativo={nome_ativo}, qnt={qnt}, preco={preco_op}")

                    key = (nr_nota, nome_ativo, op_tipo)
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
                        }

                    aggregated_trades[key]['quantidade'] += qnt
                    aggregated_trades[key]['totalValor'] += preco_op
                    ativos_negociados[nome_ativo] = op_tipo

            # Extração dos resumos na última página da nota (quando NÃO contiver 'CONTINUA...' nas áreas de resumo)
            summary_text = ""
            for key_area in ['tableClearing', 'tableBolsa', 'tableCustos']:
                rect = coordmap.get(key_area)
            if broker == "BTG" and 'tableCustos' in coordmap:
                has_continua = "CONTINUA" in page_pymu.get_textbox(coordmap['tableCustos']).upper()
            else:
                has_continua = "CONTINUA" in page_pymu.get_text().upper()
            logging.debug(f"Página {page_idx}: has_continua={has_continua}, summary_text_length={len(summary_text)}")
            if summary_text:
                logging.debug(f"Página {page_idx}: summary_text='{summary_text.strip()}'")

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
                            if line_text.strip().endswith(" D"):
                                total_irrf_deduzido += val_irrf
                            # IRRF não deve ser somado como taxa operacional, pois é antecipação de imposto
                        elif "Líquido para" in line_text:
                            val_liq = cleanup_dict['precoOperacao'](clean_value_text)
                            liquido_real_val = -abs(val_liq) if line_text.strip().endswith(" D") else abs(val_liq)
                        elif line_text.strip().endswith(" D") and not line_text.startswith("Total") and not line_text.startswith("Líquido") and not line_text.startswith("Valor líquido") and not line_text.startswith("Soma"):
                            total_taxas += cleanup_dict['precoOperacao'](clean_value_text)

                # Leitura e verificação do Resumo dos Negócios (Compras vs Vendas)
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

                # O liquido da nota pode descontar o IRRF caso ele tenha a letra D, mas para fins de L/P ele não é uma taxa
                liquido_conferido = vendas_vista - compras_vista - total_taxas - total_irrf_deduzido
                
                logging.debug(f"Página {page_idx}: Resumo Extraído -> vendas_vista={vendas_vista}, compras_vista={compras_vista}, total_taxas={total_taxas}, liquido_calc={liquido_conferido}, liquido_real={liquido_real_val}, total_irrf={total_irrf}")

                summary_data['taxas'] = total_taxas
                summary_data['irrf'] = total_irrf
                summary_data['liquidoReal'] = liquido_real_val
                summary_data['liquidoCalc'] = liquido_conferido

                if total_taxas == Decimal('0.00'):
                    logging.warning(f"⚠️ Nota sem custo detectada (taxas=0). Abortando processamento. {pdf_path}")
                    with open("notas_sem_custo.txt", "a", encoding="utf-8") as f_out:
                        f_out.write(f"{pdf_path}\n")
                    return False
                
                has_venda = any(t['operacaoTipo'] == 'V' for t in aggregated_trades.values())
                if has_venda and total_irrf == Decimal('0.00'):
                    logging.info(f"ℹ️ Nota com venda e sem IRRF debitado na nota. {pdf_path}")

    def execute_persistence(session: Session) -> bool:
        unique_assets = list(ativos_negociados.keys())
        mapped_names = resolve_asset_names(session, unique_assets)

        if mapped_names is None:
            logging.error("Execução pausada. Preencha o arquivo 'missing_assets.yaml' com os tickers correspondentes e execute novamente.")
            return False

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
            liquidoCalc=summary_data['liquidoCalc'],
            liquidoReal=summary_data['liquidoReal'],
            ativosNegociados={mapped_names.get(k, k): v for k, v in ativos_negociados.items()},
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

        for key, trade in aggregated_trades.items():
            qnt = trade['quantidade']
            total_val = trade['totalValor']
            preco_ajuste = Decimal(round(total_val / qnt, 2)) if qnt > 0 else Decimal('0.00')
            nome_fantasia = mapped_names.get(trade['nomeAtivo'], trade['nomeAtivo'])

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
