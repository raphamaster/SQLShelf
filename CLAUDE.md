# CLAUDE.md — SQLShelf

## O que é este projeto

SQLShelf é um gerenciador desktop open-source de consultas SQL: organiza, descreve, categoriza e busca queries `.sql` por conteúdo, tabela, campo ou tag. Autor: Raphael Franco. A especificação completa está em `docs/escopo-sqlshelf-python-pyside6.md` — consulte-a antes de implementar qualquer funcionalidade nova.

**Histórico:** este projeto já passou por tentativas em Go+Wails, C#+Avalonia e C#+Photino+React — todas abandonadas por complexidade de toolchain, não por problema de design. A stack atual (Python + PySide6) foi escolhida deliberadamente por simplicidade: **um processo, uma linguagem, `pip install` e roda**. Não reintroduzir complexidade de build/bridge/multi-processo.

## Ponto de partida

Já existe um **esqueleto funcional validado** com: janela PySide6, sidebar, lista de queries, editor com highlight SQL via `QSyntaxHighlighter`, e scanner de pasta que lê frontmatter YAML (`sqlshelf/core/models.py`, `frontmatter.py`, `scanner.py`, `sqlshelf/ui/highlighter.py`, `main_window.py`, `main.py`). Isso é o item 1 do backlog da Fase 1 — **já entregue**. Comece pelo item 2.

## Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| UI | PySide6 (Qt 6) + `qt-material` |
| Editor SQL | `QPlainTextEdit` + `QSyntaxHighlighter` |
| Índice | SQLite + FTS5 via `sqlite3` (stdlib) |
| Frontmatter | `PyYAML` |
| Watcher | `watchdog` |
| Extração de objetos SQL | `sqlglot` (dialect `tsql`) |
| Testes | `pytest` |
| Lint/format | `ruff` + `black` |
| Empacotamento (Fase 2) | `PyInstaller` |

## Princípios arquiteturais (invioláveis)

1. **Arquivos `.sql` no disco são a fonte da verdade.** Toda escrita vai para o arquivo; o SQLite é só índice. Nunca guardar conteúdo de query apenas no banco.
2. **O índice (`.sqlshelf/index.db` dentro da pasta do projeto do usuário) é descartável e regenerável.** Qualquer mudança de schema funciona via "apagar e reindexar".
3. **Metadados ficam em frontmatter YAML dentro de comentário SQL** (`/* --- ... --- */` no topo do arquivo). O arquivo permanece SQL válido e executável no SSMS após qualquer operação do app.

## Estrutura do projeto

```
main.py                  # entry point — python main.py
sqlshelf/
  core/                   # lógica de negócio — SEM import de PySide6
    models.py             # dataclasses: Query, Project, SearchResult
    frontmatter.py         # parse/serialize do bloco YAML
    encoding.py            # detecção UTF-8/UTF-16/Windows-1252
    scanner.py              # varredura de pasta
    sql_objects.py          # sqlglot → tabelas/colunas
    index_db.py              # SQLite, schema, FTS5, migrações
    search.py                # parsing table:/col:/tag: + bm25
    watcher.py               # watchdog + debounce
    config.py                # ~/.sqlshelf/config.json
    schema.sql               # DDL do índice
  ui/
    main_window.py
    highlighter.py
    sidebar.py
    query_list.py
    search_bar.py
    metadata_panel.py
    new_query_dialog.py
tests/
docs/
```

**`sqlshelf/core/` não importa `PySide6`.** Toda lógica é testável com `pytest` puro, sem precisar de display/Qt. Comunicação Core → UI via Qt signals/slots na camada `ui/`, nunca o inverso.

## Comandos

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

python main.py        # rodar
pytest                # testes
ruff check .           # lint
black .                # format
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

### sqlglot (`sql_objects.py`)
- `dialect="tsql"` sempre.
- `sqlglot.ParseError` → retornar conjuntos vazios, marcar `has_parse_error=True`, nunca propagar exceção ao usuário.
- CTEs (`exp.CTE`) excluídas do conjunto de tabelas.

### FTS5 (`schema.sql`, `search.py`)
- Tokenizer `unicode61 remove_diacritics 2` (busca em PT-BR sem acento).
- Sincronização `queries` ↔ `queries_fts` via triggers em `schema.sql`.
- `table:`/`col:`/`tag:` parseados em `search.py` → filtros relacionais diretos; texto livre → `MATCH` com `bm25()` + `snippet()`.
- `schema.sql` carregado via `importlib.resources`; versão em `meta('schema_version')`, migrações incrementais em `index_db.py`.

### Watcher (`watcher.py`)
- `watchdog.Observer` + debounce de ~500ms (acumular eventos antes de reindexar).
- Eventos repassados para a UI via Qt signal (`QObject` + `Signal`), nunca manipulando widgets diretamente de outra thread.

## Convenções

- **Código, comentários, commits, README: em inglês.** UI do app em inglês na Fase 1 (i18n PT-BR depois).
- Commits no padrão Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`).
- Type hints em todas as funções públicas; `from __future__ import annotations` no topo dos módulos.
- `dataclasses` para modelos (`models.py`).
- `pathlib.Path` em vez de strings para caminhos.
- `black` para formatação, `ruff` para lint — configurar em `pyproject.toml`, sem regras customizadas extensas.
- Testes obrigatórios em `tests/` para: `frontmatter.py`, `encoding.py`, `sql_objects.py`, `search.py`, `index_db.py`. Usar `pytest.mark.parametrize` para casos variados.
- Sem dependências novas sem necessidade clara — preferir stdlib.

## Fluxo de trabalho

- Desenvolvimento em **fatias verticais**, seguindo o backlog da Fase 1 (itens 2 a 12) na especificação, nesta ordem. O item 1 (esqueleto) já está pronto.
- Não implementar Fases 2-4 antes do MVP da Fase 1 estar completo e em uso real.
- Cada fatia termina com: código em `core/` (testável) + teste `pytest` correspondente + integração visível em `ui/`.
- Ao concluir um item do backlog, marcar na especificação e sugerir o próximo.

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
- Não implementar Fases 2-4 antes da Fase 1 estar em uso real.
