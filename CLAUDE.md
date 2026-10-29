# CLAUDE.md — SQLShelf

## O que é este projeto

SQLShelf é um gerenciador desktop open-source de consultas SQL: organiza, descreve, categoriza e busca queries `.sql` por conteúdo, tabela, campo ou tag. Autor: Raphael Franco. A especificação completa está em `docs/escopo-sqlshelf-python-pyside6.md` — consulte-a antes de implementar qualquer funcionalidade nova.

**Histórico:** este projeto já passou por tentativas em Go+Wails, C#+Avalonia e C#+Photino+React — todas abandonadas por complexidade de toolchain, não por problema de design. A stack atual (Python + PySide6) foi escolhida deliberadamente por simplicidade: **um processo, uma linguagem, `pip install` e roda**. Não reintroduzir complexidade de build/bridge/multi-processo.

## Estado atual

As Fases 1 e 2 do backlog da especificação estão entregues, exceto os templates, cujo código (`core/snippets.py`) está fora do menu. Versão atual em `sqlshelf/__init__.py`; histórico em `CHANGELOG.md`. Depois delas, o trabalho veio em trilhas com especificação própria em `docs/`, cada uma em fatias:

- **Redesenho de UI** — `docs/redesign-ui.md` (tokens, tema, fatias F1…F9). Consulte-o antes de mexer em qualquer coisa visual — cores, fontes e raios vêm de `ui/tokens.py`, nunca de valores soltos no widget.
- **Diagrama da consulta** — `docs/query-diagram.md`.
- **Dialetos SQL** — `docs/sql-dialects.md`.
- **Schema do índice** — referência em `docs/database-schema.md`.

## Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| UI | PySide6 (Qt 6), estilo Fusion + `QPalette` + QSS gerado a partir de tokens (`ui/tokens.py` → `ui/theme.py`) |
| Editor SQL | `QPlainTextEdit` + `QSyntaxHighlighter` |
| Índice | SQLite + FTS5 via `sqlite3` (stdlib) |
| Frontmatter | `PyYAML` |
| Watcher | `watchdog` |
| Dialetos SQL | `core/dialects.py`: T-SQL, PostgreSQL, MySQL, MariaDB, Oracle, BigQuery (detecção por sintaxe + `dialect:` no frontmatter) |
| Extração de objetos SQL | `sqlglot`, no parser do dialeto de cada query |
| Testes | `pytest` |
| Lint/format | `ruff` + `black` |
| Empacotamento | `PyInstaller` (`sqlshelf.spec`, `build_app.ps1`) + Inno Setup (Windows) / AppImage (Linux), via GitHub Actions |

## Princípios arquiteturais (invioláveis)

1. **Arquivos `.sql` no disco são a fonte da verdade.** Toda escrita vai para o arquivo; o SQLite é só índice. Nunca guardar conteúdo de query apenas no banco.
2. **O índice (`.sqlshelf/index.db` dentro da pasta do projeto do usuário) é descartável e regenerável.** Qualquer mudança de schema funciona via "apagar e reindexar".
3. **Metadados ficam em frontmatter YAML dentro de comentário SQL** (`/* --- ... --- */` no topo do arquivo). O arquivo permanece SQL válido e executável no SSMS após qualquer operação do app.

## Estrutura do projeto

```
main.py                  # entry point — python main.py
sqlshelf/
  core/                  # lógica de negócio — SEM import de PySide6
    models.py            # dataclasses: Query, SearchResult, AccessStat
    frontmatter.py       # parse/serialize do bloco YAML
    encoding.py          # detecção UTF-8/UTF-16/Windows-1252
    scanner.py           # varredura de pasta
    dialects.py          # dialeto de cada query: frontmatter ou sintaxe
    sql_segments.py      # lotes e instruções DML, no jeito de cada dialeto
    sql_objects.py       # sqlglot → tabelas/colunas/aliases
    query_graph.py       # grafo do diagrama, lido só do texto
    graph_layout.py      # layout do diagrama: grupos, camadas, coordenadas
    index_db.py          # SQLite, schema, FTS5, leitura/escrita separadas
    schema.sql           # DDL do índice
    search.py            # operadores table:/col:/tag:/date:/dialect: + ranking
    quick_search.py      # o que a busca rápida (Ctrl+P) lista
    text_match.py        # trechos destacados, com a normalização do FTS
    grouping.py          # grupos por data e por pasta
    navigation.py        # estados de navegação e histórico voltar/avançar
    folder_tree.py       # árvore de subpastas a partir dos caminhos indexados
    library_summary.py   # contagens e árvores da sidebar
    preview.py           # primeiras linhas para as miniaturas
    tag_entry.py         # normalização e sugestão de tags no formulário
    snippets.py          # templates com {{param}} (fora do menu hoje)
    watcher.py           # watchdog + debounce
    config.py            # ~/.sqlshelf/config.json
    i18n.py              # tr() e os locales em locales/{en,pt-BR,es}
    autostart.py         # iniciar com o Windows (registro)
    updater.py           # verificação de versão nova no GitHub Releases
  ui/
    tokens.py            # design tokens (cores, fontes, raios) — sem Qt
    theme.py             # Fusion + QPalette + QSS a partir dos tokens
    icons.py             # ícones SVG tingidos pelos tokens
    main_window.py       # janela, workers, ações e atalhos
    address_bar.py       # voltar/avançar/acima, breadcrumb e busca
    search_bar.py        # campo de busca com chips op:valor
    command_bar.py       # ☰, nova consulta, importar, ordenar, agrupar, exibição
    scope_bar.py         # escopo e contagem durante a busca
    sidebar.py           # biblioteca, árvore de pastas e tags
    query_list.py        # lista: Detalhes, Conteúdo e Grade
    query_grid.py        # exibição Grade
    thumbnails.py        # miniaturas do SQL, lidas em segundo plano
    metadata_panel.py    # painel de detalhes: leitura e edição
    tag_field.py         # campo de tags com chips e sugestões
    editor_strip.py      # faixa acima do editor: abas SQL | Diagrama
    code_editor.py       # editor com números de linha
    highlighter.py       # cores do SQL no dialeto da query
    diagram_view.py      # diagrama da consulta (QGraphicsView)
    diagram_window.py    # diagrama em janela própria, exportação
    command_palette.py   # busca rápida (Ctrl+P)
    status_bar.py        # contagens, progresso do índice, dialeto
    new_query_dialog.py
    template_dialog.py
    tag_widget.py, widgets.py, paint.py   # peças compartilhadas
locales/                 # traduções en, pt-BR, es
installer/               # Inno Setup e version_info do executável
tests/
docs/
```

**`sqlshelf/core/` não importa `PySide6`.** Toda lógica é testável com `pytest` puro, sem precisar de display/Qt. Comunicação Core → UI via Qt signals/slots na camada `ui/`, nunca o inverso.

## Comandos

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff, black, mypy

python main.py        # rodar
pytest                # testes
ruff check .           # lint
black .                # format
mypy                   # type check (paths vêm do pyproject.toml)
```

## Regras críticas de implementação

### Frontmatter
- `frontmatter.py` já é tolerante: YAML inválido → `metadata={}`, body = conteúdo inteiro, `has_frontmatter=False`. **Nunca lançar exceção que aborte a indexação de um arquivo.**
- Ao salvar edições: reescrever o bloco `/* --- ... --- */` preservando todos os campos do YAML original, mesmo os que a UI não edita diretamente (ex.: `created`). Atualizar `updated` para a data atual.
- Arquivo sem frontmatter: ao salvar pela primeira vez com metadados pela UI, criar o bloco do zero no topo do arquivo.

### Encoding
- `encoding.py`: detectar BOM UTF-8/UTF-16; sem BOM, tentar UTF-8 e cair para `cp1252` (Windows-1252) se decodificação falhar.
- Gravação **sempre** UTF-8 (sem BOM, a menos que o arquivo original tivesse BOM — preservar nesse caso).

### Indexação (`index_db.py`)
- Reindexar decidido por `file_mtime` primeiro; calcular `content_hash` (SHA256) só se o mtime mudou.
- Scan completo apenas na abertura do projeto ou comando explícito ("Reindex" no menu).
- Scan/indexação roda em `QThread`/`QRunnable` — **nunca bloquear a UI thread**.
- `.sqlshelf/` é ignorada no scan.
- `sqlite3.connect(..., check_same_thread=False)` com lock próprio se houver acesso de múltiplas threads (UI + watcher).

### Dialetos (`dialects.py`) e sqlglot (`sql_objects.py`)
- Cada query tem um dialeto: o de `dialect:` no frontmatter (sempre vence, aceita apelidos como `PL/SQL`, `SQL Server`, `pg`) ou, sem ele, o lido pela sintaxe em `dialects.detect()`. Sem sinal claro → `sql` (SQL genérico), lido como T-SQL.
- O parser do sqlglot vem de `Dialect.parser` (MariaDB usa `mysql`, o sqlglot não tem `mariadb`). Nunca fixar `dialect="tsql"` em código novo: passar o dialeto resolvido.
- Índice, busca (`dialect:`), diagrama, highlight e miniaturas seguem o mesmo dialeto resolvido. Detalhes e casos que não podem regredir em `docs/sql-dialects.md`.
- Leitura leniente (`ErrorLevel.IGNORE`), lote a lote (`split_batches`) e depois instrução a instrução (`split_statements`): o que o sqlglot não ler fica de fora, sem exceção ao usuário. `extract_objects()` nunca lança; não existe flag de erro de parse no índice.
- CTEs (`exp.CTE`) excluídas do conjunto de tabelas.

### FTS5 (`schema.sql`, `search.py`)
- Tokenizer `unicode61 remove_diacritics 2` (busca em PT-BR sem acento).
- A linha de `queries_fts` é escrita por `index_db._insert_prepared()`, o único código que conhece os nomes extraídos. Só a exclusão fica num trigger (`queries_ad`); não recriar triggers de INSERT/UPDATE (motivo em `docs/database-schema.md`).
- `table:`/`col:`/`tag:`/`date:`/`dialect:` parseados em `search.py` → filtros relacionais diretos; texto livre → `MATCH`, ordenado por `relevance_key()`: primeiro o nível (todas as palavras no nome, depois na descrição, depois o resto), depois `bm25()` com pesos por coluna. **Não** reintroduzir `snippet()` (removido por desempenho na 1.0.10).
- `schema.sql` carregado via `importlib.resources`; versão em `meta('schema_version')`. Versão diferente → apagar e recriar o índice (a única migração).

### Watcher (`watcher.py`)
- `watchdog.Observer` + debounce de ~500ms (acumular eventos antes de reindexar).
- Eventos repassados para a UI via Qt signal (`QObject` + `Signal`), nunca manipulando widgets diretamente de outra thread.

## Convenções

- **Código, comentários, commits, README: em inglês.** Textos da UI via `tr()` (`core/i18n.py`), com a origem em inglês e tradução nos três locales (`locales/en`, `pt-BR`, `es`).
- Commits no padrão Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`).
- Type hints em todas as funções públicas; `from __future__ import annotations` no topo dos módulos.
- `dataclasses` para modelos (`models.py`).
- `pathlib.Path` em vez de strings para caminhos.
- `black` para formatação, `ruff` para lint — configurar em `pyproject.toml`, sem regras customizadas extensas.
- Testes obrigatórios em `tests/` para: `frontmatter.py`, `encoding.py`, `sql_objects.py`, `search.py`, `index_db.py`. Usar `pytest.mark.parametrize` para casos variados.
- Sem dependências novas sem necessidade clara — preferir stdlib.

## Fluxo de trabalho

- Desenvolvimento em **fatias verticais**. Trabalho novo ganha uma especificação própria em `docs/` (como `redesign-ui.md`, `query-diagram.md`, `sql-dialects.md`), com a lista de fatias e o que não pode regredir.
- Cada fatia termina com: código em `core/` (testável) + teste `pytest` correspondente + integração visível em `ui/` + entrada no `CHANGELOG.md`.
- Ao concluir uma fatia, marcar no documento da trilha (ou no backlog da especificação) e sugerir a próxima.
- Mudou um comportamento descrito em `README.md` ou `docs/`? Atualizar o documento no mesmo commit.

## O que NÃO fazer

- Não importar `PySide6` em `sqlshelf/core/`.
- Não trocar `sqlite3` (stdlib) por driver externo.
- Não armazenar conteúdo de query apenas no SQLite.
- Não gravar arquivos do usuário em encoding diferente de UTF-8.
- Não tocar em arquivos fora da pasta do projeto aberto.
- Não indexar a pasta `.sqlshelf/`.
- Não bloquear a UI thread com I/O ou scan — usar `QThread`/`QRunnable`.
- Não lançar exceção para o usuário quando `sqlglot` ou o parser de frontmatter falharem — degradar graciosamente.
- Não reintroduzir build steps, segundos processos, ou pontes entre linguagens (a lição das tentativas anteriores).
- Não começar as Fases 3-4 da especificação sem pedido explícito.
