# AVISO: operações de day trade extraídas mas NÃO usadas

Desde outubro de 2026 o extrator lê, além dos totais, **cada operação** das
notas de Day Trade (BM&F) da XP e da BTG.

> **Essas operações são só registro. Nenhum cálculo as usa.** O resultado de
> day trade, o IRRF, o swing (`computeSwing.py`), os eventos
> (`processEvents.py`) e o frontend continuam lendo apenas o que já liam
> (`notasdaytrade`, `operacoesswingtrade` etc.). Quem quiser usá-las num
> cálculo tem de fazer isso de propósito, em outra mudança.

## Onde ficam

| O quê | Onde |
|---|---|
| Tabela | `operacoesdaytrade_detalhe` (modelo `operacoesDaytradeDetalhe` em `schemas.py`) |
| Ligação com a nota | `notaDaytradeId` → `notasdaytrade.id` |
| Extração | `extractDayDetalhe.py`, chamado por `extractDay.process_day_pdf` depois de gravar a nota |
| Regiões no PDF | `areaCodes.py`: `areaDict_btg_day_operacoes`, `areaDict_xp_day_operacoes` |
| Conferência / carga das notas antigas | `scripts/daytrade_detalhe.py` (sem `--gravar` só confere) |
| Testes (nota sintética) | `tests/test_daytrade_detalhe.py` |

Uma linha por operação, na ordem da nota: `operacaoTipo` (C/V), `mercadoria`
(ex. `WINV26`; a XP escreve `WIN V26` e os espaços são tirados),
`vencimento`, `quantidade` (negativa em `AJUPOS`), `precoAjuste`,
`tipoNegocio`, `valorOperacao` (sem sinal), `debitoCredito` (D/C, vazio
quando o valor é 0,00), `taxaOperacional`, `marcadores` (ex. `@`, "corretora
ou pessoa vinculada atuou na contraparte", na XP), mais `pagina`, `ordem`,
`hashNota`, `corretora`, `data` e `nrNota`.

Atenção: nem toda linha é day trade. A tabela de negócios da nota de day trade
também traz `NORMAL` (posição levada), `AJUPOS` (ajuste de posição) e
`TX. PERMANÊNCIA`; o campo `tipoNegocio` diz qual é.

## Garantias

- **Conferida na leitura.** A soma das operações com sinal (C = +, D = −)
  tem de ser igual a "Valor dos negócios" do resumo da nota, e a soma da taxa
  operacional igual a "Taxa operacional". Se não bater, nada é gravado para
  aquela nota, o log mostra o ERROR e o `process_notas.py` sai com código 1.
  A nota em si (`notasdaytrade`) é gravada normalmente e o PDF original é
  apagado; refaça só o detalhe com `python scripts/daytrade_detalhe.py --gravar`.
- **Idempotente.** Reprocessar a nota apaga e regrava as operações dela, numa
  transação. Nada é duplicado.
- **Em outubro de 2026**, nas 613 notas de day trade do arquivo (278 BTG, 335
  XP; 9.971 operações), a soma bateu com o valor dos negócios e com a taxa
  operacional em todas, e fechou até o líquido que o extrator já grava
  (`notasdaytrade.liquido`) em todas.

## Coordenadas

`(x0, top, x1, bottom)` em pontos, origem no canto superior esquerdo — a
convenção do pdfplumber e também a do `Rect`/`clip` do PyMuPDF, que é o que o
código usa para recortar. As notas têm mediabox em (0, 0) e rotação 0, então
não há conversão. A região inclui a linha de cabeçalho da tabela, de onde
saem as colunas.

- BTG: `(23, 179) ~ (567, 603)`
- XP: `(32, 183) ~ (566, 633)`

As notas antigas (processadas antes do detalhe existir) não têm linhas nesta
tabela até alguém rodar `python scripts/daytrade_detalhe.py --gravar`.
