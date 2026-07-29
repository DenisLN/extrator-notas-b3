import sys
from pathlib import Path

# Adiciona o diretório raiz ao sys.path para importarmos corretamente
sys.path.append(str(Path(__file__).parent.parent))

from db_connection import get_session as backend_get_session
from frontend.db_connection import get_session as frontend_get_session
from sqlalchemy import text

def test_backend_connection():
    """Testa se a conexão backend consegue conectar e realizar uma query simples."""
    try:
        with backend_get_session() as session:
            result = session.exec(text("SELECT 1")).first()
            assert result[0] == 1
    except Exception as e:
        assert False, f"Falha na conexão do backend: {e}"

def test_frontend_connection():
    """Testa se a conexão frontend consegue conectar e realizar uma query simples."""
    try:
        with frontend_get_session() as session:
            result = session.exec(text("SELECT 1")).first()
            assert result[0] == 1
    except Exception as e:
        assert False, f"Falha na conexão do frontend: {e}"

if __name__ == "__main__":
    print("Executando test_backend_connection...")
    test_backend_connection()
    print("Executando test_frontend_connection...")
    test_frontend_connection()
    print("Todos os testes passaram com sucesso!")
