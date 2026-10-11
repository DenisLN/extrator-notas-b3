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
├── extractDayDetalhe.py  # Day Trade por operação (só registro, ver NOTICE.md)
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

### 4. Configuração de credenciais e diretórios

O projeto usa um setup interativo que gera os arquivos de configuração (`.env` para o backend e `.streamlit/secrets.toml` para o frontend). Nenhum desses arquivos é versionado — estão todos no `.gitignore`.

```bash
python scripts/setup_env.py
```

O script solicitará interativamente:
- **Banco de dados**: usuário, senha, host, porta e nome do banco (padrão `notas_corretagem`).
- **Diretórios**: diretório de entrada de notas (`INPUT_DIR`, padrão `notas`) e diretório de saída para arquivamento organizado (`OUTPUT_DIR`, ex: `Z:/3 Notas Corretagem`).

### 5. Extração de notas

Coloque os PDFs das notas de corretagem na pasta `notas/` e execute o pipeline de extração correspondente ao tipo de operação.

> **💡 Mapeamento de Ativos (Swing Trade):** 
> Em notas de Swing Trade, algumas corretoras descrevem apenas o nome da empresa (ex: `PRIO ON NM`) sem o código da ação (`PRIO3`). O extrator possui heurísticas (ex: se a nota disser `FII BTLG BTLG11`, ele detecta `BTLG11`), mas quando não consegue identificar o ativo de forma segura, a extração é pausada e um arquivo chamado `missing_assets.yaml` é gerado.
> Quando isso acontecer, basta abrir este arquivo, preencher o ticker correto do ativo que faltou, e rodar a extração novamente para o sistema registrar na base de dados!

### Quem alimenta a entrada

No servidor, quem põe PDFs em `notas/` (o `INPUT_DIR`) é o **coletor de e-mails
do painel** (`/mnt/Projetos/painel/coletor_emails`, timer `coletor-emails.timer`,
diário às 21:00). Para cada e-mail de nota de negociação ele tira a senha do
PDF, grava o PDF **já sem senha** em `notas/` e, no fim da rodada, roda
`python process_notas.py` neste diretório (é daqui que vem o `.env`). As
últimas 30 linhas da saída e o código de saída vão para
`journalctl --user -u coletor-emails`.

Por isso o extrator **não precisa de cron próprio**. Rodá-lo à mão continua
valendo (a trava abaixo evita choque com o coletor).

Toda a saída vai para o stdout, com uma linha por etapa de cada nota e um
**resumo no fim** (processadas, já existentes, falhas e o motivo de cada uma),
de modo que as 30 últimas linhas guardadas pelo coletor contenham o resumo.
`-v` liga o nível DEBUG.

### Códigos de saída

| Código | Significado |
|---|---|
| `0` | Tudo certo (ou nada a processar). |
| `1` | Alguma falha: nota com erro (ou gravada, mas com ERROR no log, ex. operação duplicada não gravada), PDF ilegível/com senha, `pg_dump` falhou (nada é processado sem o snapshot), recálculo do swing falhou, detalhe de day trade não conferiu, ou erro inesperado. O PDF original de uma nota que falhou fica em `notas/` e é tentado de novo na próxima execução. |
| `2` | Nenhuma falha, mas alguma nota de swing ficou **pausada** esperando `missing_assets.yaml` (ativo sem ticker). Preencha o arquivo e rode de novo. Sem terminal (coletor/systemd) a nota é pausada em vez de esperar o ENTER. |
| `3` | Outra execução já está rodando (trava ocupada); nada foi feito. |

Uma nota ruim não derruba o lote: **cada PDF da entrada é processado num
subprocesso** (o próprio `process_notas.py`, em modo interno `--um-arquivo`).
O processo principal só pega a trava, lista a entrada, faz o snapshot
(`pg_dump`), chama os subprocessos (vários ao mesmo tempo, ver
"Paralelismo" abaixo), repassa a saída deles para o log e
lê o resultado para o resumo; quem apaga o original é o processo principal,
só quando o subprocesso diz que todas as notas do arquivo deram certo. Se o
subprocesso quebrar, travar (limite de `NOTAS_TIMEOUT_POR_ARQUIVO` segundos,
padrão 1800) ou morrer sem resultado, aquele PDF conta como falha, fica na
entrada e os outros seguem. O erro vai para o log com o arquivo, a nota e a
etapa. "Nota já registrada" (reenvio da
corretora, reprocessamento) não é falha: nada é duplicado e o original é
apagado.

### Trava

No começo, o `process_notas.py` pega uma trava exclusiva (`fcntl.flock`; no
Windows, `msvcrt.locking`) no arquivo `notas/.processando`. Uma segunda
execução ao mesmo tempo (ex.: à mão enquanto o coletor roda) sai na hora com a
mensagem "Outra execução do process_notas.py já está em andamento" e o código
`3`. O arquivo `.processando` fica no diretório de propósito; não apague.
`--restore`/`--restore-last` não pegam a trava.

### Paralelismo e gravação no banco

Os PDFs são lidos **em paralelo**: um subprocesso por thread do processador
(`os.cpu_count()`; troque com `NOTAS_PROCESSOS`), nunca mais que o número de
PDFs. A saída de cada PDF aparece inteira no log, na ordem em que terminam.

As **gravações não** são paralelas. Tudo o que lê e grava no banco para decidir
o destino de uma nota (a nota já existe? o ativo já tem ticker? o
`missing_assets.yaml`; a gravação da nota e das operações; o recálculo do
swing do CPF) roda dentro de uma **região crítica**: uma trava consultiva do
Postgres (`pg_advisory_lock`), única para o projeto, em
[`travaBanco.py`](travaBanco.py). Ler o PDF é o que demora; gravar leva
milissegundos, então a fila é curta. Assim a mesma nota em dois PDFs lidos ao
mesmo tempo é gravada uma vez só (o outro PDF vê "já registrada"), e dois
recálculos do mesmo CPF não se intercalam.

- A trava é do banco, não do processo: vale também entre o coletor no
  servidor e uma execução à mão em outra máquina contra o mesmo banco.
- Um subprocesso morto (timeout, quebra) solta a trava, porque o Postgres a
  libera quando a conexão cai.
- Se a trava não vier em `NOTAS_ESPERA_TRAVA_BANCO` segundos (padrão 900), a
  nota falha com essa mensagem e o PDF fica na entrada.
- A nota de swing e as operações dela são gravadas numa transação só: ou
  entram juntas, ou nada entra.
- As tabelas são criadas (`create_all`) pelo processo principal antes de abrir
  os subprocessos.
- A nota é isolada no `OUTPUT_DIR` num temporário e só depois ganha o nome
  final, então nunca fica um PDF pela metade, nem quando dois subprocessos
  isolam a mesma nota.

### Day trade por operação (só registro)

Além dos totais da nota, as operações de cada nota de day trade (XP e BTG) são
gravadas em `operacoesdaytrade_detalhe`. **Não são usadas em nenhum cálculo.**
Veja [NOTICE.md](NOTICE.md).

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