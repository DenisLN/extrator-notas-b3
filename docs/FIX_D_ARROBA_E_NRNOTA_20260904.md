# Correção: prefixo "D@" ausente e campo nrNota faltando em `notasDaytrade`

Registro do que foi investigado, corrigido e migrado em 2026-09-04. Separado
por bug, cada seção diz o que foi **testado de verdade** contra o banco/disco
reais e o que foi apenas **assumido/derivado**.

---

## Bug 1 — `relativePath` sem o prefixo "D@"

### O que a auditoria pediu para confirmar, e o que a auditoria própria encontrou

Rodei minha própria auditoria (script Python via SSH em `aspire5050`,
comparando as 556 linhas de `notasdaytrade` contra os arquivos reais em
`/mnt/3 Notas Corretagem`) antes de mexer em qualquer coisa. Os números
batem exatamente com os que vieram na tarefa: **127 linhas batem direto,
429 só batem se acrescentar "D@" ao nome do arquivo, 0 sem nenhum match**.

Só que ao investigar a fundo **por que** cada grupo batia (ou não), a causa
raiz é diferente da hipótese inicial:

- **Fiz um censo completo do disco** (`os.walk` em `3 Notas Corretagem`,
  730 PDFs no total): **todos os 556 arquivos de Day Trade já têm o
  prefixo "D@" no nome, sem exceção**. Não existe nenhum arquivo de Day
  Trade em disco sem o prefixo.
- As 127 linhas que "batiam direto" batiam porque o `relativePath` **já
  estava gravado corretamente no banco**, com "D@" e usando barra normal
  (`/`) — são as notas mais recentes (ids a partir de ~815, datas de
  jun/jul/2026), processadas depois que o `process_notas.py` passou a
  montar o nome isolado com o prefixo (commit `8dd0d97`, 25/08/2026).
- As 429 linhas que só batiam com fallback tinham `relativePath` **sem
  "D@" e com barra invertida** (`3 Notas Corretagem\...\02-02-2026@...`)
  — são notas mais antigas, de antes dessa mudança. O arquivo real
  sempre teve "D@"; só a coluna no banco ficou desatualizada.

**Ou seja: o disco nunca esteve inconsistente — só o banco.** A frase "o
disco em si já está inconsistente entre os próprios arquivos" da hipótese
inicial não se confirmou; o censo completo (730/730 arquivos, 556 com
"D@", 174 com "S@", 0 sem prefixo) mostra o disco 100% consistente desde
sempre. Isso também bate com o comentário que já existia em
`painel/app/corretagem/routes.py::nota_file_url` ("os arquivos ... no
disco têm um prefixo D@ ... mas o campo relativePath gravado no Postgres
... não tem esse prefixo") — o `painel` já tinha diagnosticado
corretamente o lado do bug, só não tinha corrigido a origem ainda.

**Consequência prática**: não precisou renomear nenhum arquivo físico.
Só foi preciso corrigir os dados no banco (e o código que os gera).

### Causa raiz no código

`extractDay.py::process_day_pdf` monta `relativePath` só removendo o
prefixo de disco/pasta do caminho recebido — ele nunca adiciona "D@"
sozinho, só reflete o nome do arquivo que recebeu. O prefixo "D@" é
adicionado em `process_notas.py` (linha ~206), quando o PDF original é
isolado e copiado para `3 Notas Corretagem/...` antes de chamar
`process_day_pdf`. Esse fluxo (`process_notas.py`) já está correto hoje.

O problema é que `extractDay.py` também pode ser chamado sozinho (bloco
`if __name__ == "__main__"` no fim do arquivo, ou testes/scripts antigos)
apontando direto para um PDF que ainda não passou pelo isolamento — nesse
caso o "D@" nunca é adicionado. Foi assim, historicamente, que as 429
linhas antigas foram gravadas sem o prefixo (o arquivo foi isolado/
renomeado com "D@" depois, por outro processo, sem que o banco fosse
atualizado).

### Correção aplicada

Em `extractDay.py`, logo depois de montar `rel_path`, adicionei uma
normalização defensiva: troca `\` por `/` e garante que o nome do arquivo
comece com "D@", incluindo o prefixo se ele não estiver lá. Isso deixa o
comportamento simétrico ao caso feliz de hoje (via `process_notas.py`) e
também protege qualquer chamada futura de `process_day_pdf` fora desse
fluxo.

**Testado de verdade**: rodei `process_day_pdf(..., dry_run=True)` duas
vezes no servidor: uma vez no arquivo real (já com "D@") e outra numa
cópia temporária **sem** o prefixo — confirmei que em ambos os casos o
`relativePath` gravado sai com "D@" e barra normal. A cópia de teste foi
apagada depois do teste.

### Migração dos dados existentes

1. **Backup antes de qualquer UPDATE**:
   - `pg_dump -c` completo do banco `notas_corretagem` →
     `backups/notasDaytrade_backup_20260904_141847.sql`
   - Export CSV das 556 linhas de `notasdaytrade` como estavam antes →
     `backups/notasdaytrade_pre_migration_20260904.csv`
   - Plano de migração completo (linha a linha: caminho antigo, caminho
     novo, nrNota extraído do PDF, nrNota do nome do arquivo) →
     `backups/migration_plan_20260904.json`
   - Todos os arquivos acima estão em `backups/`, que já é ignorado pelo
     git (não vai para o repositório, como já era a convenção do
     projeto).
2. Rodei um script (via SSH, direto no servidor) que, para cada uma das
   556 linhas: localizou o arquivo real em disco (com ou sem "D@" no
   nome do jeito que estava gravado), reabriu o PDF de verdade com
   PyMuPDF, confirmou a corretora (BTG/XP) e comparou com a coluna
   `corretora` do banco, e calculou o novo `relativePath` normalizado
   (barra normal + "D@" garantido).
   - **556/556 processados sem nenhum problema**: nenhum arquivo
     faltando, nenhuma corretora divergente.
3. Executei o `UPDATE` das 556 linhas dentro de uma transação (com
   `ROLLBACK` automático em caso de erro) — só deu `COMMIT` porque a
   checagem de consistência (nenhuma linha nula) passou antes.

### Validação final (rodada depois do commit, contra o banco de produção)

- Reexecutei a auditoria original: **556 match direto, 0 precisando de
  fallback, 0 sem match**.
- `SELECT` de conferência: 556 linhas, todas com "D@" em `relativePath`,
  0 linhas com barra invertida restante.

---

## Bug 2 — `notasDaytrade` sem o campo `nrNota`

### Confirmação (não assumida) de que o número do nome do arquivo é o número real da nota

Antes de tratar o segundo segmento do nome do arquivo (ex.: `423420` em
`D@02-02-2026@423420@11353922.pdf`) como o número oficial da nota, abri 9
PDFs reais (6 da XP, 3 da BTG, arquivos de datas/clientes diferentes) e
comparei três fontes:

1. O texto impresso no PDF sob o rótulo **"Nr. nota"** (confirmado
   visualmente no texto extraído da página, ex.: `NOTA DE NEGOCIAÇÃO` /
   `Nr. nota` / `Total líquido da nota` para XP; `NOTA DE CORRETAGEM` /
   `Nr. nota` para BTG).
2. O valor extraído pela coordenada `nrNota` que já existe em
   `areaCodes.py` (`areaDict_xp_day['nrNota']` e
   `areaDict_btg_day['nrNota']`) — coordenadas que **já existiam no
   código antes desta tarefa**, só não eram usadas porque o campo não
   existia no schema.
3. O segundo segmento do nome do arquivo.

Nos 9/9 casos testados, os três valores bateram exatamente (ex.: PDF
mostra "302.773", `clean_int` limpa para `302773`, nome do arquivo tem
`...@302773@...`). Depois, ao rodar a migração completa (ver abaixo), o
mesmo teste foi repetido para **as 556 linhas**, não só a amostra: 0
divergências entre nome do arquivo e número extraído do PDF.

**Conclusão: o segundo segmento do nome do arquivo é mesmo o número da
nota impresso no PDF, e a extração via `extractDay.py` já capturava esse
valor corretamente — ele só era descartado.**

### Por que o valor era descartado

`extractDay.py` já lia `nrNota` do PDF (o dicionário de coordenadas tem a
chave `nrNota` para os dois corretores, e `cleanupFunctions.cleanup_dict`
já tinha `"nrNota": clean_int`) e até **validava a presença desse campo**
antes de aceitar a nota como válida (linha ~87: `if not linha.get('data')
or not linha.get('nCliente') or not linha.get('nrNota')`). Só que na hora
de montar o objeto (`notasDaytrade(**d)`), como a classe não tinha o
campo `nrNota`, o Pydantic/SQLModel **descartava silenciosamente** essa
chave extra — confirmei isso instanciando o modelo antigo manualmente com
um `nrNota=123` extra: o objeto é criado sem erro nenhum, mas o valor
some do `model_dump()`. Não havia mensagem de erro nem aviso — por isso
passou despercebido.

### Correção aplicada

- `schemas.py`: adicionado `nrNota: int` em `notasDaytrade`, na mesma
  posição relativa que `registroNotasSwing` usa.
- `extractDay.py`: **nenhuma mudança adicional foi necessária** — como o
  valor já estava sendo extraído e colocado em `linha['nrNota']`, bastou
  o schema passar a aceitar o campo para ele começar a ser persistido.
  Confirmado com um dry-run real (arquivo `D@01-06-2026@302773@...pdf`):
  o dump do registro já sai com `'nrNota': 302773`.

### Migração de schema e dados

- `ALTER TABLE notasdaytrade ADD COLUMN "nrNota" INTEGER` (nullable
  primeiro).
- Backfill das 556 linhas com o valor **re-extraído do PDF real**
  (não só do nome do arquivo, para robustez — embora os dois tenham
  batido 100% das vezes) durante a mesma passada que corrigiu o
  `relativePath`.
- Depois do backfill, confirmado `0` linhas com `nrNota` nulo, então
  `ALTER TABLE notasdaytrade ALTER COLUMN "nrNota" SET NOT NULL`.
- Nome da coluna no Postgres ficou `"nrNota"` (mixed-case, entre aspas),
  igual ao padrão já usado para `"semiLiquido"`, `"nCliente"`,
  `"hashNota"` nessa mesma tabela — confirmado via `\d notasdaytrade`
  antes e depois da migração.

### Validação final

- `SELECT COUNT(*), COUNT("nrNota") FROM notasdaytrade` → 556 / 556.
- Coluna é `NOT NULL` (confirmado no `\d notasdaytrade` final).

---

## O que NÃO foi feito (fora do escopo, ou decisão do usuário)

- **Nenhum arquivo em disco foi renomeado.** A hipótese de que 127
  arquivos precisariam ganhar o prefixo "D@" não se confirmou — o censo
  completo do disco mostrou que os 556 arquivos de Day Trade já tinham
  "D@" desde antes desta tarefa. Só a coluna no banco precisou de
  correção.
- **`painel`**: só atualizei `app/schemas_corretagem.py` (adicionei
  `nrNota: int`, espelhando o schema real) porque já estava mexendo no
  schema espelho. **Não removi** o fallback em
  `app/corretagem/routes.py::nota_file_url` (tenta sem "D@", depois com
  "D@") nem o comentário em `app/corretagem/calculations.py` — o
  fallback continua funcionando normalmente (só ficou redundante, já que
  agora todo `relativePath` novo/corrigido já bate direto). Fica a
  critério do usuário limpar esse fallback depois, se quiser — não é
  urgente e não quebra nada deixá-lo como está. Essa edição em `painel`
  **não foi commitada** (o pedido de commit era só para o repositório
  `notascorretagem`).
- **Constraint única de `notasDaytrade`**: não alterei
  `UniqueConstraint("corretora", "cpf", "data", "nCliente")` para incluir
  `nrNota` (como `registroNotasSwing` faz). Não foi pedido, e mudar isso
  teria implicações diferentes (Day Trade assume 1 nota por dia por
  cliente; Swing permite mais de uma). Se no futuro aparecer mais de uma
  nota de Day Trade no mesmo dia para o mesmo cliente, essa constraint
  pode rejeitar a segunda — vale o usuário ter isso em mente, mas é um
  comportamento pré-existente, não algo introduzido por esta correção.

## Backups (não commitados, ficam em `backups/`)

- `notasDaytrade_backup_20260904_141847.sql` — dump completo do banco
  antes da migração.
- `notasdaytrade_pre_migration_20260904.csv` — as 556 linhas exatas
  antes do UPDATE.
- `migration_plan_20260904.json` — plano linha a linha (caminho
  antigo/novo, nrNota extraído, resultado da validação) usado para
  aplicar a migração.

## Commit

`schemas.py` e `extractDay.py` foram commitados juntos no repositório
`notascorretagem` (branch atual). Os outros arquivos que já estavam
modificados no working tree antes desta tarefa (`.gitignore`,
`cleanupFunctions.py`, `extractSwing.py`, `process_notas.py`,
`scripts/testAreas.py`) **não foram tocados nem incluídos** neste commit
— são mudanças pré-existentes, não relacionadas a este bug.
