import os
import re
import yaml
import logging
import threading
from sqlmodel import Session, select
from schemas import registroNomeAtivos

YAML_PATH = "missing_assets.yaml"
TICKER_REGEX = re.compile(r'\b[A-Z0-9]{4}\d{1,2}\b')
yaml_lock = threading.Lock()


def classificar_tipo_ativo(ticker: str) -> str:
    """
    Classifica um ticker B3 pelo sufixo numérico.
    ticker já deve ser o nome fantasia (ex: "BRCO11", "RADL3", "ROXO34").
    """
    codigo = ticker.split()[0].upper()
    match = re.search(r'(\d+)$', codigo)
    if not match:
        return "ACAO"
    sufixo = int(match.group(1))
    if sufixo == 11:
        return "FII"
    if sufixo in {33, 34, 35, 39}:
        return "BDR"
    return "ACAO"

def normalize_name(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(s or "").upper())

def extract_ticker_from_name(nome_ativo: str) -> str | None:
    match = TICKER_REGEX.search(nome_ativo.upper())
    if match:
        return match.group(0)
    return None

def resolve_asset_names(session: Session, asset_names: list[str]) -> dict[str, str] | None:
    """
    Verifica se os nomes de ativos extraídos existem na tabela registroNomeAtivos.
    Se não constarem no banco, tenta extrair o ticker usando heurística de regex.
    Caso não seja identificado, grava o nome no arquivo 'missing_assets.yaml' para preenchimento manual.
    """
    # 1. Processar arquivo YAML se ele já existir (preenchido pelo usuário)
    with yaml_lock:
        if os.path.exists(YAML_PATH):
            try:
                with open(YAML_PATH, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                
                new_entries = False
                for nome_ativo, ticker in data.items():
                    if ticker and str(ticker).strip():
                        ticker_clean = str(ticker).strip().upper()
                        existing = session.exec(
                            select(registroNomeAtivos).where(registroNomeAtivos.nomeAtivo == nome_ativo)
                        ).first()
                        if not existing:
                            reg = registroNomeAtivos(
                                nomeAtivo=nome_ativo,
                                nomeFantasia=ticker_clean,
                                tipoAtivo=classificar_tipo_ativo(ticker_clean),
                            )
                            session.add(reg)
                            new_entries = True
                
                if new_entries:
                    session.commit()
                    logging.info("Novos mapeamentos de ativos salvos em registroNomeAtivos.")
            except Exception as e:
                logging.error(f"Erro ao ler/processar {YAML_PATH}: {e}")

    # 2. Buscar no banco todos os nomes requisitados
    stmt = select(registroNomeAtivos).where(registroNomeAtivos.nomeAtivo.in_(asset_names))
    db_entries = session.exec(stmt).all()
    mapped = {entry.nomeAtivo: entry.nomeFantasia for entry in db_entries}

    missing = [name for name in asset_names if name not in mapped]

    # 2.5. Busca flexível no banco ignorando espaços e caracteres especiais (ex: BRADESCO PNN1 vs BRADESCO PN)
    if missing:
        all_db_entries = session.exec(select(registroNomeAtivos)).all()
        still_missing = []
        new_matches = False
        for name in missing:
            name_norm = normalize_name(name)
            match_found = False
            for entry in all_db_entries:
                db_norm = normalize_name(entry.nomeAtivo)
                if db_norm and len(db_norm) >= 4:
                    if db_norm == name_norm or db_norm in name_norm or name_norm in db_norm:
                        mapped[name] = entry.nomeFantasia
                        reg = registroNomeAtivos(
                            nomeAtivo=name,
                            nomeFantasia=entry.nomeFantasia,
                            tipoAtivo=classificar_tipo_ativo(entry.nomeFantasia),
                        )
                        session.add(reg)
                        new_matches = True
                        match_found = True
                        break
            if not match_found:
                still_missing.append(name)
        if new_matches:
            session.commit()
            logging.info("Mapeamentos aproximados encontrados no banco e cadastrados automaticamente.")
        missing = still_missing

    # 3. Heurística: tentar extrair o ticker diretamente do nome do ativo via regex
    still_missing = []
    new_auto_entries = False

    for name in missing:
        ticker_found = extract_ticker_from_name(name)
        if ticker_found:
            reg = registroNomeAtivos(
                nomeAtivo=name,
                nomeFantasia=ticker_found,
                tipoAtivo=classificar_tipo_ativo(ticker_found),
            )
            session.add(reg)
            mapped[name] = ticker_found
            new_auto_entries = True
        else:
            still_missing.append(name)

    if new_auto_entries:
        session.commit()
        logging.info("Tickers identificados por heurística salvos automaticamente em registroNomeAtivos.")

    missing = still_missing

    # 4. Se ainda houver nomes faltantes sem regex/banco, grava no YAML e solicita preenchimento
    if missing:
        with yaml_lock:
            # Verifica novamente no banco para o caso de outra thread já ter cadastrado
            stmt = select(registroNomeAtivos).where(registroNomeAtivos.nomeAtivo.in_(missing))
            db_entries = session.exec(stmt).all()
            newly_mapped = {entry.nomeAtivo: entry.nomeFantasia for entry in db_entries}
            
            missing = [m for m in missing if m not in newly_mapped]
            mapped.update(newly_mapped)

            if not missing:
                return mapped

            while missing:
                yaml_content = {}
                if os.path.exists(YAML_PATH):
                    try:
                        with open(YAML_PATH, "r", encoding="utf-8") as f:
                            yaml_content = yaml.safe_load(f) or {}
                    except Exception:
                        pass
                
                for m in missing:
                    if m not in yaml_content:
                        yaml_content[m] = ""

                with open(YAML_PATH, "w", encoding="utf-8") as f:
                    yaml.dump(yaml_content, f, allow_unicode=True, default_flow_style=False)

                input(f"\n[AÇÃO NECESSÁRIA] {len(missing)} ativo(s) pendentes: {missing}. Preencha-os no '{YAML_PATH}', salve o arquivo e pressione ENTER aqui no terminal para continuar...")
                
                try:
                    with open(YAML_PATH, "r", encoding="utf-8") as f:
                        yaml_content = yaml.safe_load(f) or {}
                    
                    still_missing = []
                    for m in missing:
                        ticker = yaml_content.get(m)
                        if ticker and str(ticker).strip():
                            ticker_clean = str(ticker).strip().upper()
                            mapped[m] = ticker_clean
                            reg = registroNomeAtivos(
                                nomeAtivo=m,
                                nomeFantasia=ticker_clean,
                                tipoAtivo=classificar_tipo_ativo(ticker_clean),
                            )
                            session.add(reg)
                        else:
                            still_missing.append(m)
                            print(f"-> Ativo '{m}' ainda está vazio no arquivo.")
                    
                    if not still_missing:
                        session.commit()
                        logging.info("Novos mapeamentos de ativos salvos com sucesso no banco de dados!")
                        break
                    else:
                        missing = still_missing
                except Exception as e:
                    print(f"Erro ao ler o arquivo YAML: {e}")
                    
        # Não retornamos None, pois agora o loop garante o preenchimento (ou o script fica pausado)

    # 5. Se todos os ativos foram resolvidos e não há pendências no YAML, remove o arquivo
    with yaml_lock:
        if os.path.exists(YAML_PATH):
            try:
                with open(YAML_PATH, "r", encoding="utf-8") as f:
                    yaml_data = yaml.safe_load(f) or {}
                if not any(not v or not str(v).strip() for v in yaml_data.values()):
                    os.remove(YAML_PATH)
            except Exception:
                pass

    return mapped


def resolve_asset_names_with_type(session: Session, asset_names: list[str]) -> dict[str, dict] | None:
    """
    Equivalente a resolve_asset_names, mas retorna um dict enriquecido com tipoAtivo:
        { nomeAtivo: {"nomeFantasia": str, "tipoAtivo": str} }
    Retorna None se resolve_asset_names retornar None.
    """
    mapped = resolve_asset_names(session, asset_names)
    if mapped is None:
        return None

    # Buscar tipoAtivo para todos os nomeFantasia resolvidos
    fantasias = list(set(mapped.values()))
    stmt = select(registroNomeAtivos).where(registroNomeAtivos.nomeFantasia.in_(fantasias))
    db_entries = session.exec(stmt).all()
    tipo_por_fantasia: dict[str, str] = {e.nomeFantasia: e.tipoAtivo for e in db_entries if e.tipoAtivo}

    result: dict[str, dict] = {}
    for nome_ativo, nome_fantasia in mapped.items():
        tipo = tipo_por_fantasia.get(nome_fantasia) or classificar_tipo_ativo(nome_fantasia)
        result[nome_ativo] = {"nomeFantasia": nome_fantasia, "tipoAtivo": tipo}

    return result
