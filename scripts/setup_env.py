import os
from pathlib import Path

def main():
    print("=== Configuração de Cofres de Segurança ===")
    print("Este script gerará automaticamente os arquivos .env e .streamlit/secrets.toml")
    print("---------------------------------------------------------")
    
    user = input("Usuário do banco de dados (ex: postgres): ").strip()
    password = input("Senha do banco de dados: ").strip()
    host = input("Host (ex: localhost): ").strip()
    port = input("Porta (ex: 5432): ").strip()
    dbname = input("Nome do banco de dados (ex: notas_corretagem): ").strip()
    
    if not all([user, password, host, port, dbname]):
        print("Erro: Todos os campos são obrigatórios!")
        return
        
    db_url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    
    # Root of the project
    root_dir = Path(__file__).parent.parent
    
    # 1. Configurar .env
    env_path = root_dir / ".env"
    with open(env_path, "w") as f:
        f.write(f'DATABASE_URL="{db_url}"\n')
    print(f"\n[OK] Arquivo gerado em: {env_path}")
    
    # 2. Configurar .streamlit/secrets.toml
    streamlit_dir = root_dir / ".streamlit"
    streamlit_dir.mkdir(exist_ok=True)
    secrets_path = streamlit_dir / "secrets.toml"
    with open(secrets_path, "w") as f:
        f.write(f'DATABASE_URL = "{db_url}"\n')
    print(f"[OK] Arquivo gerado em: {secrets_path}")
    
    print("\n✅ Ambiente configurado com sucesso! Seus dados não serão comitados no git.")

if __name__ == "__main__":
    main()
