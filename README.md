# 📊 Extrator de Notas de Corretagem B3

Extrai, estrutura e visualiza operações de **Day Trade** e **Swing Trade** a partir de notas de corretagem em PDF de corretoras brasileiras. Os dados são persistidos em **PostgreSQL** e apresentados via dashboards **Streamlit**.

---

## Stack

| Camada | Tecnologias |
|---|---|
| Extração de PDF | PyMuPDF · pdfplumber |
| Backend / ORM | Python 3.10+ · SQLModel · SQLAlchemy · psycopg2 |
| Frontend | Streamlit · Pandas · openpyxl |
| Utilitários | xxhash (dedup) · PyYAML |

## Corretoras suportadas

| Corretora | Status |
|---|---|
| XP Investimentos | ✅ Suportada |
| BTG Pactual | ✅ Suportada |

> [!NOTE]
> Notas de outras corretoras (Clear, Rico, NuInvest, etc.) **não funcionarão** — a localização do texto no PDF varia completamente entre corretoras.

## Estrutura do projeto

```
notascorretagem/
├── areaCodes.py          # Coordenadas de extração por corretora
├── extractDay.py         # Parser de notas Day Trade
├── extractSwing.py       # Parser de notas Swing Trade
├── process_notas.py      # Orquestrador do pipeline de extração
├── processEvents.py      # Processamento de eventos corporativos
├── assetMapper.py        # Mapeamento de tickers / ativos
├── cleanupFunctions.py   # Limpeza e normalização de dados
├── schemas.py            # Modelos SQLModel (tabelas do banco)
├── db_connection.py      # Conexão com PostgreSQL
│
├── frontend/
│   ├── app.py            # Entrypoint Streamlit
│   └── pages/
│       ├── 1_Day_Trade.py
│       └── 2_Swing_Trade.py
│
├── scripts/
│   ├── setup_env.py      # Gerador de .env e secrets.toml
│   ├── processAllNotes.py
│   └── ...               # Scripts de manutenção / debug
│
├── tests/
│   └── test_db_connection.py
│
├── notas/                # PDFs de notas (não versionado)
└── requirements.txt
```

---

## Começando

### 1. Pré-requisitos

- **Python 3.10+**
- **PostgreSQL** instalado e rodando (veja a seção abaixo)

### 2. Instalando o PostgreSQL

Este projeto **requer um servidor PostgreSQL funcionando** para armazenar as operações extraídas. Sem ele, nenhum dado será persistido e o projeto não funcionará.

<details>
<summary>🐧 Linux (Arch / Manjaro)</summary>

Siga o guia oficial do Arch Wiki:
https://wiki.archlinux.org/title/PostgreSQL

Resumo rápido:

```bash
sudo pacman -S postgresql
sudo -iu postgres initdb -D /var/lib/postgres/data
sudo systemctl enable --now postgresql

# Criar o banco
sudo -iu postgres createdb notas_corretagem
```

</details>

<details>
<summary>🪟 Windows</summary>

Baixe o instalador oficial:
https://www.postgresql.org/download/windows/

O instalador já inclui o pgAdmin e configura o serviço automaticamente. Após a instalação:

```powershell
# Via psql (incluso no instalador)
psql -U postgres
CREATE DATABASE notas_corretagem;
\q
```

</details>

Após a instalação, certifique-se de que o banco `notas_corretagem` existe:

```sql
psql -U postgres -c "SELECT datname FROM pg_database WHERE datname = 'notas_corretagem';"
```

### 3. Clonando e criando o ambiente virtual

```bash
git clone https://github.com/SEU-USUARIO/extrator-notas-b3.git
cd extrator-notas-b3

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

> [!IMPORTANT]
> Os diretórios `venv/` e `env/` estão no `.gitignore`. Crie o ambiente virtual com qualquer um desses nomes para que ele não seja versionado acidentalmente.

### 4. Configuração de credenciais

O projeto usa um setup interativo que gera os arquivos de credenciais (`.env` para o backend e `.streamlit/secrets.toml` para o frontend). Nenhum desses arquivos é versionado — estão todos no `.gitignore`.

```bash
python scripts/setup_env.py
```

O script vai pedir: **usuário**, **senha**, **host**, **porta** e **nome do banco** (use `notas_corretagem`).

### 5. Extração de notas

Coloque os PDFs das notas de corretagem na pasta `notas/` e execute o pipeline de extração correspondente ao tipo de operação.

### 6. Interface visual

```bash
streamlit run frontend/app.py
```

Acesse `http://localhost:8501` para ver dashboards de Day Trade e Swing Trade.

---

## Contribuindo

Contribuições são muito bem-vindas — especialmente para **suporte a novas corretoras**.

O processo é simples: abra `areaCodes.py` e mapeie as coordenadas `(x0, y0, x1, y1)` das áreas de texto no PDF da nova corretora. Uma vez que as coordenadas estejam corretas, todo o restante do fluxo funciona automaticamente.

1. Faça um **Fork** do repositório
2. Mapeie as áreas da nova corretora em `areaCodes.py`
3. Teste com PDFs reais (não os versione!)
4. Envie um **Pull Request**

> [!CAUTION]
> **Nunca faça commit de PDFs reais.** Notas de corretagem contêm dados altamente sensíveis (CPF, patrimônio, conta bancária). Use apenas dados anonimizados para testes e reports.

---

## Licença

MIT.