# SQLShelf — Escopo do Projeto (Python 3.12 + PySide6)

> Gerenciador desktop open-source de consultas SQL: organize, descreva, categorize e encontre suas queries por conteúdo, tabela ou campo — sem perder a propriedade dos arquivos.

**Identidade do projeto:**
- Nome: **SQLShelf** · repo: `github.com/raphamaster/SQLShelf`
- Licença: GNU GPL v3.0 (arquivo `LICENSE`)
- Plataformas: Windows (alvo principal), Linux e macOS funcionam pelo mesmo código (PySide6 é cross-platform)
- Pasta de índice por projeto do usuário: `.sqlshelf/index.db`
- Config global: `~/.sqlshelf/config.json`

---

## 0. Estado atual

As **Fases 1 e 2 estão entregues** (seção 7), exceto os templates parametrizáveis, cujo código existe mas está fora do menu; a Fase 1 saiu na 1.0.0. Depois delas, o trabalho veio em trilhas com especificação própria, fora deste backlog:

| Trilha | Documento | Versões |
|---|---|---|
| Redesenho de UI (tema por tokens, barras, sidebar, lista, painel, busca) | `docs/redesign-ui.md` | 1.1.0 – 1.2.0 |
| Diagrama da consulta | `docs/query-diagram.md` | 1.3.0 |
| Dialetos SQL (PostgreSQL, MySQL, MariaDB, Oracle, BigQuery) | `docs/sql-dialects.md` | 1.4.0 |

O schema do índice está descrito em `docs/database-schema.md`; o que mudou em cada versão, no `CHANGELOG.md`.

---

## 1. Princípios arquiteturais (invioláveis)

1. **Arquivos `.sql` no disco são a fonte da verdade.** O app nunca guarda conteúdo de query só no banco. Toda escrita vai para o arquivo; o SQLite é índice.
2. **O índice (`.sqlshelf/index.db` dentro da pasta do projeto do usuário) é descartável e regenerável.** Mudou o schema? Apaga e reindexa. Nenhum dado vive só no índice.
3. **Metadados ficam em frontmatter YAML dentro de comentário SQL** (`/* --- ... --- */` no topo do arquivo). O arquivo permanece SQL válido e executável no SSMS após qualquer operação do app.

---

## 2. Stack de tecnologia

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Linguagem | **Python 3.12** | Já conhecido; sem curva de linguagem nova |
| UI | **PySide6** (Qt 6) | `pip install`, sem build step, sem processo separado |
| Editor SQL | `QPlainTextEdit` + `QSyntaxHighlighter` | Built-in no PySide6, zero dependência extra |
| Tema | Fusion + `QPalette` + QSS gerado de tokens (`ui/tokens.py` → `ui/theme.py`) | Sem dependência; substituiu o `qt-material` na 1.1.0 (ver `docs/redesign-ui.md`) |
| Índice | SQLite + FTS5 via `sqlite3` (stdlib) | Sem driver externo |
| Frontmatter | `PyYAML` | Parser YAML maduro |
| Watcher | `watchdog` | Multiplataforma, debounce manual |
| Extração de objetos SQL | `sqlglot` | Pure Python, lê T-SQL, PostgreSQL, MySQL, Oracle e BigQuery (MariaDB pelo parser MySQL), sem fallback regex necessário |
| Testes | `pytest` | Padrão de fato |
| Lint/format | `ruff` + `black` | Configuração mínima, rápidos |
| Type check | `mypy` | Roda sobre `sqlshelf/`, `main.py` e `tests/` |
| Empacotamento | `PyInstaller` + Inno Setup (Windows) / AppImage (Linux) | Instalador e zip portátil, gerados pelo GitHub Actions a cada tag `v*`; local: `build_app.ps1` |

**Princípio geral:** preferir stdlib e bibliotecas puro-Python. Evitar pacotes com extensões compiladas (exceto PySide6, que já fornece wheels pré-compiladas para todas as plataformas).

---

## 3. Formato do arquivo `.sql` (frontmatter)

```sql
/* ---
title: Documentos por última versão
tags: [relatorio, certisign, cross-apply]
description: >
  Conta documentos considerando apenas a versão mais recente
  de cada um, usando CROSS APPLY TOP(1) ordenado por data.
created: 2026-06-11
updated: 2026-06-11
--- */

SELECT d.DocumentoId, v.Versao, v.DataCriacao
FROM dbo.Documentos AS d
CROSS APPLY (
    SELECT TOP (1) Versao, DataCriacao
    FROM dbo.Versoes v
    WHERE v.DocumentoId = d.DocumentoId
    ORDER BY v.DataCriacao DESC
) AS v;
```

Regras:
- Bloco `/* --- ... --- */` no topo, conteúdo YAML válido.
- Arquivos sem frontmatter são válidos: `title` = nome do arquivo sem extensão, `has_frontmatter = False`.
- YAML malformado nunca derruba a indexação: `metadata = {}`, body = conteúdo inteiro, `has_frontmatter = False`.
- Gravação sempre em UTF-8. Leitura detecta UTF-8/UTF-16/Windows-1252 (Fase 1, item 10).
- Ao salvar edições, o bloco de frontmatter é reescrito de forma íntegra — nunca perder campos não editados pela UI.
- `dialect:` é opcional (`tsql`, `postgres`, `mysql`, `mariadb`, `oracle`, `bigquery`, ou apelidos como `PL/SQL`, `SQL Server`, `pg`). Sem ele, o dialeto é lido pela sintaxe; ver `docs/sql-dialects.md`.

---

## 4. Estrutura do projeto

```
SQLShelf/
├── main.py                        # entry point — python main.py
├── requirements.txt               # PySide6, PyYAML, watchdog, sqlglot, pytest
├── requirements-dev.txt           # ruff, black, mypy
├── pyproject.toml                 # config do ruff/black/mypy/pytest
├── sqlshelf.spec                  # PyInstaller
├── build_app.ps1                  # build local do Windows (pasta + zip portátil)
├── README.md · CHANGELOG.md · CLAUDE.md
├── LICENSE                        # GPL v3
├── installer/                     # Inno Setup e version_info do executável
├── locales/                       # en, pt-BR, es
├── images/                        # logo, ícone do app e screenshots do README
├── .github/workflows/             # testes em cada push/PR; release em cada tag v*
├── sqlshelf/
│   ├── core/                      # lógica de negócio — SEM import de PySide6
│   └── ui/                        # widgets, janela, workers
├── tests/                         # pytest; os testes de UI rodam offscreen
└── docs/                          # esta especificação e as trilhas
```

A lista de módulos de `core/` e `ui/`, com o papel de cada um, está no `CLAUDE.md`.

**`sqlshelf/core/` não importa nada de `PySide6`.** Toda lógica é testável isoladamente com `pytest`, sem precisar de display/Qt.

---

## 5. Schema do índice SQLite (com FTS5)

O DDL atual (`sqlshelf/core/schema.sql`), tabela por tabela, com triggers, índices e a regra de versão, está em **`docs/database-schema.md`** — a referência única, para não manter duas cópias. Em resumo:

- `queries` (uma linha por `.sql`, com `dialect`), `tags` + `query_tags`, `query_objects` (tabelas, colunas, aliases, procedures, funções), `favorites`, `recently_viewed`, `access_log` e `meta`.
- `queries_fts` (FTS5) sobre `title`, `description`, `body` e `objects`, com tokenizer `unicode61 remove_diacritics 2` → "relatorio" encontra "relatório". A linha é escrita pelo indexador, que é quem conhece os nomes extraídos; só a exclusão fica num trigger.
- Em Python, `sqlite3.connect()` com SQLite ≥ 3.43 (Python 3.12 já embute uma versão compatível) suporta FTS5 nativamente — sem extensão extra.
- `schema.sql` é lido via `importlib.resources` e aplicado na primeira abertura; se `meta('schema_version')` for diferente da versão do código, o índice é apagado e refeito (a única migração).

**Sintaxe de busca:**

| Entrada | Comportamento |
|---|---|
| `cross apply versão` | FTS5 em title+description+body+objects; primeiro as que têm todas as palavras no nome, depois na descrição, depois o resto, cada nível por `bm25()` com pesos por coluna |
| `table:Documentos` | filtro NOCASE em `query_objects` (type=table) |
| `col:DataCriacao` | filtro NOCASE em `query_objects` (type=column) |
| `tag:relatorio` | filtro por tag |
| `date:15/06/2026` | modificadas naquele dia (hora local) |
| `dialect:oracle` | filtro pelo dialeto da query; aceita os apelidos do frontmatter (`plsql`, `sqlserver`, `pg`…) |
| `table:Doc tag:relatorio erros` | interseção dos filtros + FTS no texto livre |

`sqlshelf/core/search.py` faz o parsing da string de busca, separando prefixos `table:`/`col:`/`tag:`/`date:`/`dialect:` do texto livre, e monta a query SQL combinando filtros relacionais com `MATCH`.

---

## 6. Extração de objetos via sqlglot

`sqlshelf/core/sql_objects.py` → `extract_objects(sql_body, dialect=None)` devolve tabelas, colunas, aliases, procedures e funções:

- O dialeto vem de cada query (`dialects.resolve()`: `dialect:` do frontmatter ou a sintaxe; sem sinal claro, T-SQL). `tsql` cobre `CROSS APPLY`, CTEs, funções de janela, `TOP`, hints; os demais dialetos usam o parser correspondente do sqlglot (`Dialect.parser`).
- A leitura é leniente (`ErrorLevel.IGNORE`): mais da metade dos scripts T-SQL reais tem `GO`, blocos CLR ou sintaxe sqlcmd e nunca parseia limpo, mas ainda cita tabelas reais. O texto é lido lote a lote (`sql_segments.split_batches`) e cada instrução DML é lida de novo sozinha (`split_statements`), porque código procedural esconde as instruções que contém.
- CTEs, variáveis `@x`, palavras de bloco (`END`, `DECLARE`…) e o alias de `UPDATE t … FROM Tabela t` ficam fora das tabelas; nomes com cara de lixo da recuperação de erro são descartados.
- Nunca lança: o que não deu para ler fica de fora, sem aviso nem exceção para o usuário.
- Resultado alimenta `query_objects` e a coluna `objects` do FTS.

---

## 7. Backlog por fases

### Fase 1 — MVP
*Critério de pronto: você abandona o Explorer para achar queries.*

1. ✅ **Bootstrap** (entregue): janela, sidebar, lista, editor com highlight, scanner de pasta com frontmatter tolerante.
2. ✅ **Índice SQLite + FTS5**: `index_db.py` — criação do `.sqlshelf/index.db`, aplicação do `schema.sql`, indexação inicial a partir do `scanner.py` (inserção em `queries`, `tags`, `query_tags`).
3. ✅ **Indexação incremental**: comparar `file_mtime`; calcular `content_hash` (SHA256) só se mudou; reindexar apenas o necessário.
4. ✅ **Busca**: `search_bar.py` + `search.py` — sintaxe `table:`/`col:`/`tag:`, resultados com `snippet()` e ranking `bm25()`, populando `query_list.py`. *(Depois: `snippet()` removido por desempenho na 1.0.10; ranking por nível + `bm25()` com pesos na 1.4.1.)*
5. ✅ **Extração de objetos**: `sql_objects.py` (sqlglot) alimentando `query_objects`; `metadata_panel.py` exibe "Esta query usa: tabelas X, Y / colunas Z".
6. ✅ **Edição e salvamento**: tornar o editor editável com toggle; salvar preserva frontmatter (reescreve só os campos alterados via `frontmatter.py`); reindexa o arquivo salvo.
7. ✅ **Nova query ("colar e salvar")**: `new_query_dialog.py` — colar SQL, preencher título/tags/descrição, escolher subpasta, cria o `.sql` com frontmatter.
8. ✅ **Tags na sidebar**: `sidebar.py` lista tags distintas (via `tags`/`query_tags`); clicar filtra a `query_list`.
9. ✅ **Watcher**: `watcher.py` (watchdog) com debounce ~500ms, reindexando arquivos alterados/criados/excluídos fora do app, atualizando a UI via Qt signal.
10. ✅ **Encoding**: `encoding.py` — detecção UTF-8 (com/sem BOM) / UTF-16 / fallback Windows-1252 na leitura; gravação sempre UTF-8.
11. ✅ **Config & projetos recentes**: `config.py` — `~/.sqlshelf/config.json` com lista de projetos recentes; menu para trocar de projeto.
12. ✅ **Ações de sistema**: botões "Open in SSMS", "Reveal Folder", "Copy" via `subprocess`/`os.startfile`.

### Fase 2 — Refinamento
- ✅ Favoritos e "abertas recentemente".
- ✅ Duplicar query como base para nova demanda (`Ctrl+D`); copiar sem frontmatter.
- ⏸ Snippets/templates parametrizáveis: `core/snippets.py` (`{{param}}`, pasta `~/.sqlshelf/templates`) e `ui/template_dialog.py` existem, mas a opção "New from Template" saiu do menu Arquivo e hoje não é alcançável pela UI.
- ✅ Atalho `Ctrl+P` (busca rápida; refeita no redesenho, F8).
- ✅ Busca reversa: botão direito numa tabela → "Buscar em todas as consultas" (painel de detalhes e diagrama).
- ✅ Empacotamento com PyInstaller + GitHub Actions: instalador Inno Setup e zip portátil no Windows, AppImage e zip portátil no Linux.

### Fase 3 — Estruturas de banco
- Importar DDL/schema (`INFORMATION_SCHEMA` ou script SSMS) para catálogo por projeto.
- Autocomplete básico no editor baseado no catálogo (QCompleter).
- Validação: coluna/tabela referenciada não existe no catálogo importado.

### Fase 4 — Versionamento e modelagem
- Integração Git via `GitPython` ou `subprocess git`: status, histórico por arquivo, diff.
- Módulo de modelagem de dados como projeto irmão.

---

## 8. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Encoding legado (Win-1252) quebra índice/busca | `encoding.py` desde o item 10 da Fase 1; gravação sempre UTF-8 |
| Frontmatter corrompido por edição externa | Parser tolerante (já no esqueleto): o arquivo é indexado como se não tivesse frontmatter (`metadata={}`, body inteiro, `has_frontmatter=False`) |
| `sqlglot` não parsear T-SQL não-padrão, ou a query ser lida no dialeto errado | Leitura leniente, por lote e por instrução, sem exceção ao usuário; o usuário corrige o dialeto no formulário (`dialect:`) |
| Escopo crescer antes do MVP rodar | Regra de ouro: nada de Fase 2+ antes de uso real por 1 semana |
| Pastas grandes (milhares de arquivos) | Indexação incremental por `mtime`+hash; `QThread`/`QRunnable` para scan sem travar a UI |
| Performance da UI com listas grandes | Lista em `QTreeView` + model próprio desde o redesenho (F4); a Grade é pintada à mão, com miniaturas lidas em segundo plano |

---

## 9. Setup do ambiente (Windows nativo)

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Rodar
python main.py

# Testes
pytest

# Lint/format/tipos
ruff check .
black .
mypy
```

**Extensões VS Code:**
```
ms-python.python
ms-python.vscode-pylance
charliermarsh.ruff
```

---

## 10. Decisões de design da UI

Desde a 1.1.0 a UI segue `docs/redesign-ui.md`, que é a referência para tudo que é visual. Em resumo:

- **Layout:** `QSplitter` horizontal de 3 painéis — sidebar (biblioteca, pastas, tags), lista de queries, painel de detalhes (metadados + abas SQL | Diagrama) —, com barra de endereço e barra de comandos em cima; a `QMenuBar` fica oculta e os menus vão no ☰.
- **Tema:** Fusion + `QPalette` + QSS gerado dos tokens (`ui/tokens.py` → `ui/theme.py`), escuro e claro, com troca ao vivo.
- **Tipografia:** Cascadia Mono 13 px no editor e nos nomes de objetos, com fallback para Consolas e depois para a fonte mono do sistema.
- **Feedback de indexação:** barra de status com o estado do índice, barra de progresso durante a indexação e quantas pastas estão sendo observadas; sem diálogo modal.
- **Atalhos:** a tabela completa está no `README.md`.
