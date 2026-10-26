# Dialetos SQL

> **Escopo:** além de T-SQL, ler consultas em PostgreSQL, MySQL, MariaDB, Oracle (PL/SQL) e BigQuery: indexar, buscar, desenhar o diagrama e colorir cada uma no seu dialeto. O `CLAUDE.md` continua valendo inteiro: core sem PySide6, nada de I/O nem sqlglot na UI thread, arquivos no disco são a fonte da verdade, textos via i18n em EN / PT-BR / ES, sem dependência nova.

## Decisão

**Detecção automática, com correção do usuário.** O app lê o dialeto pela sintaxe; quando erra, o usuário escolhe no formulário de edição e a escolha vai para o frontmatter como `dialect:`. O arquivo continua SQL válido: o campo mora no bloco `/* --- … --- */`, como os demais metadados.

Sem sinal claro, a consulta é **SQL genérico** (`sql`) e é lida como T-SQL, exatamente como toda consulta era lida antes desta trilha. Uma biblioteca só de T-SQL não muda de comportamento.

## Dialetos

| Chave | Rótulo | Parser do sqlglot | Apelidos aceitos em `dialect:` |
|---|---|---|---|
| `tsql` | T-SQL | `tsql` | `mssql`, `SQL Server`, `MS SQL Server`, `Azure SQL` |
| `postgres` | PostgreSQL | `postgres` | `postgresql`, `pg`, `pgsql`, `psql`, `PL/pgSQL` |
| `mysql` | MySQL | `mysql` | — |
| `mariadb` | MariaDB | `mysql` (o sqlglot não tem `mariadb`) | `maria` |
| `oracle` | Oracle | `oracle` | `PL/SQL`, `Oracle SQL` |
| `bigquery` | BigQuery | `bigquery` | `bq`, `GoogleSQL` |
| `sql` | SQL | `tsql` | `ansi`, `generic` |

Os apelidos são comparados sem caixa, espaços, `-`, `_` nem `/`: `PL/SQL`, `pl-sql` e `plsql` são o mesmo nome. Um nome que não está na tabela é ignorado (a consulta cai na detecção) e **nunca** é apagado do arquivo.

## Arquitetura

| Módulo | Camada | Responsabilidade |
|---|---|---|
| `core/dialects.py` | core | `DIALECTS`, `GENERIC`, apelidos (`normalize`), detecção (`detect`), resolução (`resolve`, `settle`). Nunca lança exceção |
| `core/sql_segments.py` | core | Lotes e instruções no jeito de cada dialeto (`GO`, `/`, `DELIMITER`, corpos `$$`) |
| `core/sql_objects.py` | core | Tabelas e colunas com o parser do dialeto |
| `core/query_graph.py` | core | Diagrama no dialeto declarado, ou no detectado quando não há declaração |
| `core/index_db.py` | core | Coluna `queries.dialect` (schema 6); o índice guarda o dialeto **resolvido** |
| `core/search.py`, `core/quick_search.py` | core | Operador `dialect:` |
| `core/frontmatter.py` | core | `apply_edits(..., dialect=)` grava ou remove `dialect:` |
| `ui/metadata_panel.py` | ui | Campo Dialeto no formulário de edição |
| `ui/editor_strip.py`, `ui/status_bar.py` | ui | Nome do dialeto e tooltip com a origem |
| `ui/highlighter.py`, `ui/thumbnails.py` | ui | Cores por dialeto no editor e nas miniaturas da grade |

## Resolução

```
resolve(declarado, corpo):
    dialect: do frontmatter normaliza?  → esse dialeto, origem "frontmatter"
    senão detect(corpo) achou um?       → esse dialeto, origem "detected" (+ sinais)
    senão                               → SQL genérico, origem "default"
```

`settle(declarado, detecção)` faz o mesmo com uma detecção já feita: o worker de carga roda `detect()` fora da UI thread e a janela só combina o resultado com o frontmatter.

## Detecção — `detect()`

- Cada sinal é uma regex com dialeto, peso (1 a 3) e rótulo. **Cada sinal conta uma vez**, por mais que apareça.
- Comentários e strings são apagados antes (`code_view`): `-- LIMIT 5, 10` e `'GO'` não dizem nada. Os sinais que dependem do texto cru (`# comentário`, `q'[…]'`, `/*M!`) olham o original.
- Vence o dialeto de maior soma se ela chegar a **2** (`_THRESHOLD`) e **nenhum outro empatar**. Empate ou soma baixa → SQL genérico.
- MySQL e MariaDB disputam como uma família; se a família vence, os sinais próprios da MariaDB escolhem MariaDB.
- Só os primeiros **200 KB** são lidos (`_SCAN_LIMIT`): um script gerado de vários MB não trava a indexação.

Sinais principais (lista completa em `_SIGNS`):

| Dialeto | Sinais |
|---|---|
| T-SQL | linha `GO`, `[colchetes]`, `DECLARE @`, `@@ROWCOUNT`, `TOP`, `#temp`, `NOLOCK`, `dbo.`, `CROSS/OUTER APPLY`, `SET NOCOUNT`, `VARCHAR(MAX)`, `sp_executesql`, `OUTPUT inserted.`, `sys.`, `N'…'`, funções (`GETDATE`, `DATEADD`, `CHARINDEX`…) |
| PostgreSQL | `::tipo`, corpos `$$`, `LANGUAGE plpgsql`, `ILIKE`, `JSONB`, `TIMESTAMPTZ`, `SERIAL`, `DISTINCT ON`, `ON CONFLICT`, `generate_series`, `pg_*`, `RAISE NOTICE` |
| MySQL | `` `a`.`b` ``, `` `nome` ``, `DELIMITER`, `LIMIT n, m`, `AUTO_INCREMENT`, `ENGINE=`, `ON DUPLICATE KEY UPDATE`, `INSERT IGNORE`/`REPLACE INTO`, `UNSIGNED`, `SHOW TABLES…`, `# comentário`, funções (`GROUP_CONCAT`, `DATE_FORMAT`…) |
| MariaDB | `WITH SYSTEM VERSIONING`, `ENGINE=Aria/ColumnStore/…`, `JSON_DETAILED`/`COLUMN_CREATE`, `/*M!` |
| Oracle | `FROM DUAL`, `ROWNUM`, `SYSDATE`, `NVL`, `DECODE`, `VARCHAR2`, `NUMBER(`, `CONNECT BY`, `(+)`, linha `/`, `DBMS_*`, `MINUS`, `q'[…]'`, `%TYPE`, `CREATE PACKAGE`, `BULK COLLECT`, `.NEXTVAL`, `ALL_/DBA_/USER_TABLES…` |
| BigQuery | `` `projeto.dataset.tabela` ``, `SAFE_CAST`, `STRUCT<`/`ARRAY<`, `SELECT AS STRUCT`, `* EXCEPT(`, `_TABLE_SUFFIX`, `INT64`, `AS STRING`, `[OFFSET(n)]`, `QUALIFY`, funções (`DATE_DIFF`, `FORMAT_DATE`…) |

Sinais fracos (peso 1: `LIMIT`, `TO_CHAR`, `ELSIF`, `UNNEST`, `IFNULL`, `RETURNING`, `N'…'`) são comuns a vários dialetos: um sozinho não chega ao limiar, e como costumam aparecer em mais de um dialeto ao mesmo tempo, quase sempre só desempatam.

## O que cada parte faz com o dialeto

### Índice e busca
- `scanner.py` lê `dialect:` do frontmatter (normalizado); `index_db.py` resolve e grava a chave em `queries.dialect`. Tabelas e colunas são extraídas com o parser desse dialeto.
- Schema **6**: o índice antigo é apagado e reconstruído na primeira abertura (princípio do índice descartável).
- `dialect:x` na busca e na busca rápida filtra por `queries.dialect`, aceitando os mesmos apelidos do frontmatter (`dialect:plsql` acha Oracle). Nome desconhecido não acha nada.

### Lotes e instruções (`sql_segments.py`)
- T-SQL: lotes por linha `GO`. Oracle: por linha `/`. MySQL/MariaDB: pelo delimitador definido em `DELIMITER`. PostgreSQL/BigQuery: o script inteiro é um lote.
- Corpo `$$ … $$` do PostgreSQL é lido como código; `q'[…]'` do Oracle vira string comum antes de tokenizar.
- Cada dialeto acrescenta as palavras procedurais que encerram uma instrução. `END`, `PRINT` e `FETCH` não deixam mais o sqlglot engolir o que vem depois até o próximo `;` (correção que vale também para T-SQL).
- `SELECT … INTO x` preenche variáveis fora do T-SQL (não cria tabela); nomes de rotinas não contam como tabela.

### Formulário de edição
- Campo **Dialeto**: `Automático (detectado: X)` (ou `nenhum detectado`), cada dialeto, `SQL genérico`.
- Automático **remove** `dialect:` do arquivo; um dialeto grava a chave; SQL genérico grava `dialect: sql` (força a leitura T-SQL sem detecção).
- Se o arquivo já declara o mesmo dialeto com outra grafia (`PL/SQL`), a grafia original fica. Um nome que o app não conhece nunca é removido, nem com Automático.

### Faixa do editor e barra de status
- Mostram o rótulo do dialeto resolvido (antes era "T-SQL" fixo). Tooltip:
  - `X: declarado no cabeçalho do arquivo`
  - `X: lido pela sintaxe (GO, [identifiers])`
  - `Nenhum dialeto detectado: lido como SQL genérico`
- A barra de status esconde o dialeto quando não há consulta aberta.

### Diagrama
- Usa o dialeto declarado; sem declaração, `build_query_graph` detecta pelo corpo (no worker). A chave do cache inclui o dialeto declarado, então trocar `dialect:` redesenha.

### Cores
- `SqlHighlighter(document, dialect)`; `set_dialect()` só recolore quando o dialeto muda.
- MySQL/MariaDB/BigQuery: `#` é comentário, `"…"` é string, `` `nome` `` é nome. PostgreSQL/Oracle: `"…"` é nome. Oracle: `q'[…]'` é string. T-SQL/genérico: `[nome]` e `"…"` são nomes, `#temp` continua nome.
- Nome entre delimitadores não mostra a palavra-chave que soletra (`[order]`, `` `order` ``).
- Cada dialeto soma suas palavras-chave (`LIMIT`, `RETURNING`, `QUALIFY`, `CONNECT BY`, `GO`…). Dialeto desconhecido é colorido como T-SQL.
- Miniaturas da grade usam o dialeto do índice; a chave do cache de pixmaps inclui o dialeto.

## Limitações aceitas

- MySQL `REPLACE INTO … SELECT` indexa só a parte do `SELECT`.
- Oracle `FOR r IN (…) LOOP`: o registro `r` aparece como coluna.
- `DUAL` é indexada como tabela.
- Highlight é por linha: `q'[…]'` que atravessa linhas, ou um `'` sobrando depois de um `q'…'`, pode colorir errado o resto da linha.
- A detecção só vê sintaxe própria de um dialeto: uma consulta escrita para Oracle mas sem nenhum sinal (`SELECT a FROM t WHERE b = 1`) é SQL genérico. Sinais depois dos primeiros 200 KB não contam. Nos dois casos, basta declarar `dialect:`.

## Não pode regredir

- [ ] Biblioteca só de T-SQL: mesmas tabelas, colunas, diagrama e cores de antes (genérico = T-SQL)
- [ ] `#temp` em T-SQL nunca vira comentário; `GO` só é sinal em linha própria
- [ ] Comentários e strings não influenciam a detecção
- [ ] Frontmatter: `apply_edits` preserva campos não editados e a grafia de `dialect:`; nome desconhecido nunca é apagado
- [ ] YAML inválido ou `dialect:` não-string não lança exceção
- [ ] `detect()` e `sqlglot` só fora da UI thread (worker de carga, indexador, worker do diagrama)
- [ ] Schema do índice com versão nova sempre que a coluna mudar (apagar e reindexar)
- [ ] Textos novos nos três locales (EN, PT-BR, ES)

## Testes

| Arquivo | O que cobre |
|---|---|
| `tests/test_dialects.py` | Sinais de cada dialeto, comentários e strings ignorados, empate → genérico, família MySQL/MariaDB, apelidos, `resolve` = `settle`, limite de 200 KB |
| `tests/test_sql_segments.py` | `/` do Oracle, `DELIMITER`, corpos `$$`, `q'[…]'`, palavras procedurais de cada dialeto, instrução depois de `END`/`PRINT` |
| `tests/test_sql_objects.py`, `tests/test_query_graph.py` | Tabelas, colunas e grafo em cada dialeto; `SELECT … INTO` de variáveis; rotinas fora das tabelas |
| `tests/test_index_db.py`, `tests/test_search.py`, `tests/test_quick_search.py` | Coluna `dialect`, schema 6, operador `dialect:` com apelidos |
| `tests/test_frontmatter.py` | Gravar, manter grafia, remover e nunca remover nome desconhecido |
| `tests/test_metadata_panel.py` | Opções do campo, rótulo Automático, valor devolvido, PT-BR, `clear()` |
| `tests/test_editor_strip.py`, `tests/test_main_window.py` | Faixa, barra de status, tooltip por origem, diagrama no dialeto declarado, highlight seguindo a consulta |
| `tests/test_highlighter.py`, `tests/test_thumbnails.py` | Sintaxe de cada dialeto, dialeto desconhecido = T-SQL, `set_dialect`, miniatura por dialeto |

## Fatias

Branch `feat/sql-dialects`, um commit por fatia.

- [x] **L1 — Detecção (core):** `dialects.py` com sinais, apelidos e resolução
- [x] **L2 — Leitura por dialeto (core):** lotes, instruções, objetos e grafo seguem o dialeto
- [x] **L3 — Índice e busca:** coluna `dialect`, schema 6, operador `dialect:`
- [x] **L4 — Formulário:** campo Dialeto e `dialect:` no frontmatter
- [x] **L5 — Exibição:** faixa do editor e barra de status com a origem
- [x] **L6 — Diagrama:** desenha no dialeto declarado
- [x] **L7 — Cores:** highlight e miniaturas por dialeto
- [x] **L8 — Docs:** este documento, `CLAUDE.md`, README, escopo, CHANGELOG, ajuda da busca
