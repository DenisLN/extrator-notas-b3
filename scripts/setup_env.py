import os
import re
from pathlib import Path
from urllib.parse import urlparse, unquote

def parse_existing_env(env_path: Path) -> dict:
    """Extrai variáveis de um arquivo .env existente."""
    data = {}
    if not env_path.exists():
        return data
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            data[k] = v
    return data

def parse_db_url(db_url: str):
    """Tenta quebrar uma URL do PostgreSQL em partes componentes."""
    if not db_url:
        return None
    try:
        parsed = urlparse(db_url)
        return {
            "user": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
            "host": parsed.hostname,
            "port": str(parsed.port) if parsed.port else "5432",
            "dbname": parsed.path.lstrip("/") if parsed.path else None,
        }
    except Exception:
        return None

def prompt_value(label: str, default: str = None, is_password: bool = False) -> str:
    """Solicita um valor ao usuário exibindo o valor padrão se existir."""
    if default:
        if is_password:
            prompt_str = f"{label} [Pressione Enter para manter a senha atual]: "
        else:
            prompt_str = f"{label} [{default}]: "
    else:
        prompt_str = f"{label}: "
    
    val = input(prompt_str).strip()
    if not val and default is not None:
        return default
    return val

def main():
    root_dir = Path(__file__).parent.parent
    env_path = root_dir / ".env"
    existing_env = parse_existing_env(env_path)

    existing_db = parse_db_url(existing_env.get("DATABASE_URL", ""))

    print("=== Configuração de Cofres de Segurança e Diretórios ===")
    print("Este script configurará interativamente os arquivos .env e .streamlit/secrets.toml.")
    print("Pressione Enter para aceitar o valor padrão indicado entre colchetes [].")
    print("----------------------------------------------------------------------\n")

    # 1. Configurações de Banco de Dados
    print("--- 1. Banco de Dados PostgreSQL ---")
    default_user = (existing_db.get("user") if existing_db else None) or "postgres"
    default_host = (existing_db.get("host") if existing_db else None) or "localhost"
    default_port = (existing_db.get("port") if existing_db else None) or "5432"
    default_dbname = (existing_db.get("dbname") if existing_db else None) or "notas_corretagem"
    existing_pass = existing_db.get("password") if existing_db else None

    user = prompt_value("Usuário do banco de dados", default=default_user)
    password = prompt_value("Senha do banco de dados", default=existing_pass, is_password=True)
    host = prompt_value("Host do banco de dados", default=default_host)
    port = prompt_value("Porta do banco de dados", default=default_port)
    dbname = prompt_value("Nome do banco de dados", default=default_dbname)

    if not all([user, host, port, dbname]):
        print("\n[ERRO] Usuário, host, porta e nome do banco são obrigatórios!")
        return

    db_url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

    # 2. Configurações de Diretórios
    print("\n--- 2. Diretórios de Processamento ---")
    default_input = existing_env.get("INPUT_DIR") or "notas"
    
    default_output_candidate = existing_env.get("OUTPUT_DIR")
    if not default_output_candidate:
        if Path("Z:/3 Notas Corretagem").exists():
            default_output_candidate = "Z:/3 Notas Corretagem"
        elif Path("z:/3 Notas Corretagem").exists():
            default_output_candidate = "z:/3 Notas Corretagem"
        elif Path("/mnt/3 Notas Corretagem").exists():
            default_output_candidate = "/mnt/3 Notas Corretagem"
        else:
            default_output_candidate = "./3 Notas Corretagem"

    input_dir = prompt_value("Diretório de entrada de notas (PDFs brutos)", default=default_input)
    output_dir = prompt_value("Diretório de saída (PDFs organizados por cliente/ano/mês)", default=default_output_candidate)

    # 3. Gerar .env
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f'DATABASE_URL="{db_url}"\n')
        f.write(f'INPUT_DIR="{input_dir}"\n')
        f.write(f'OUTPUT_DIR="{output_dir}"\n')
    print(f"\n[OK] Arquivo gerado em: {env_path}")

    # 4. Gerar .streamlit/secrets.toml
    streamlit_dir = root_dir / ".streamlit"
    streamlit_dir.mkdir(exist_ok=True)
    secrets_path = streamlit_dir / "secrets.toml"
    with open(secrets_path, "w", encoding="utf-8") as f:
        f.write(f'DATABASE_URL = "{db_url}"\n')
        f.write(f'INPUT_DIR = "{input_dir}"\n')
        f.write(f'OUTPUT_DIR = "{output_dir}"\n')
    print(f"[OK] Arquivo gerado em: {secrets_path}")

    print("\n[SUCESSO] Ambiente configurado com sucesso! Seus dados não serão versionados no git.")

if __name__ == "__main__":
    main()
