# SQLShelf — Escopo do Projeto (Python 3.12 + PySide6)

> Gerenciador desktop open-source de consultas SQL: organize, descreva, categorize e encontre suas queries por conteúdo, tabela ou campo — sem perder a propriedade dos arquivos.

**Identidade do projeto:**
- Nome: **SQLShelf** · repo: `github.com/raphaelfranco/sqlshelf`
- Licença: Apache 2.0
- Plataformas: Windows (alvo principal), Linux e macOS funcionam pelo mesmo código (PySide6 é cross-platform)
- Pasta de índice por projeto do usuário: `.sqlshelf/index.db`
- Config global: `~/.sqlshelf/config.json`

---

## 0. Ponto de partida

Já existe um **esqueleto funcional validado**: janela PySide6 com sidebar, lista de queries e editor com highlight SQL, lendo uma pasta de arquivos `.sql` com frontmatter YAML. Arquivos do esqueleto:

```
main.py
requirements.txt
sqlshelf/
├── core/
│   ├── models.py        # dataclass Query
│   ├── frontmatter.py   # parser do bloco /* --- ... --- */
│   └── scanner.py        # varre pasta, monta lista de Query
└── ui/
    ├── highlighter.py     # QSyntaxHighlighter para SQL via regex
    └── main_window.py     # janela principal, splitter de 3 painéis
```

**Este esqueleto é o item 1 do backlog da Fase 1 — já entregue.** Cole-o no novo repositório e continue a partir do item 2.

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
| Tema | `qt-material` | Tema escuro moderno com 1 linha de código |
| Índice | SQLite + FTS5 via `sqlite3` (stdlib) | Sem driver externo |
| Frontmatter | `PyYAML` | Parser YAML maduro |
| Watcher | `watchdog` | Multiplataforma, debounce manual |
| Extração de objetos SQL | `sqlglot` | Pure Python, bom suporte a T-SQL, sem fallback regex necessário |
| Testes | `pytest` | Padrão de fato |
| Lint/format | `ruff` + `black` | Configuração mínima, rápidos |
| Empacotamento (Fase 2) | `PyInstaller` | Quando for distribuir |

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

---

## 4. Estrutura do projeto

```
sqlshelf/
├── main.py                       # entry point — python main.py
├── requirements.txt
├── pyproject.toml                # config do ruff/black/pytest
├── README.md
├── LICENSE                        # MIT
├── .gitignore                     # inclui .sqlshelf/, venv/, __pycache__/
├── sqlshelf/
│   ├── __init__.py
│   ├── core/                      # lógica de negócio — SEM import de PySide6
│   │   ├── __init__.py
│   │   ├── models.py              # Query, Project, SearchResult (dataclasses)
│   │   ├── frontmatter.py         # parse/serialize do bloco YAML
│   │   ├── encoding.py            # detecção UTF-8 / UTF-16 / Windows-1252
│   │   ├── scanner.py             # varredura de pasta
│   │   ├── sql_objects.py         # sqlglot → tabelas/colunas/procs
│   │   ├── index_db.py            # conexão SQLite, schema, FTS5, migrações
│   │   ├── search.py              # parsing table:/col:/tag: + bm25
│   │   ├── watcher.py             # watchdog + debounce
│   │   ├── config.py              # ~/.sqlshelf/config.json
│   │   └── schema.sql             # DDL do índice (FTS5 + triggers)
│   └── ui/
│       ├── __init__.py
│       ├── main_window.py         # janela principal
│       ├── highlighter.py         # QSyntaxHighlighter
│       ├── sidebar.py             # árvore de pastas + tags
│       ├── query_list.py          # lista/resultados
│       ├── search_bar.py          # busca com sintaxe especial
│       ├── metadata_panel.py      # título, tags, descrição, objetos
│       └── new_query_dialog.py    # "colar e salvar"
├── tests/
│   ├── test_frontmatter.py
│   ├── test_encoding.py
│   ├── test_sql_objects.py
│   ├── test_search.py
│   └── test_index_db.py
└── docs/
    └── escopo-sqlshelf-python-pyside6.md
```

**`sqlshelf/core/` não importa nada de `PySide6`.** Toda lógica é testável isoladamente com `pytest`, sem precisar de display/Qt.

---

## 5. Schema do índice SQLite (com FTS5)

```sql
-- .sqlshelf/index.db — regenerável a qualquer momento a partir dos arquivos

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
); -- schema_version, project_root, last_full_scan

CREATE TABLE queries (
    id           INTEGER PRIMARY KEY,
    rel_path     TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    description  TEXT,
    body         TEXT NOT NULL,
    file_mtime   INTEGER NOT NULL,
    file_size    INTEGER NOT NULL,
    content_hash TEXT NOT NULL,       -- SHA256
    has_frontmatter INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE query_tags (
    query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (query_id, tag_id)
);

CREATE TABLE query_objects (
    query_id    INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    object_type TEXT NOT NULL CHECK (object_type IN ('table','column','procedure','function')),
    object_name TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (query_id, object_type, object_name)
);
CREATE INDEX ix_query_objects_name ON query_objects (object_name, object_type);

CREATE VIRTUAL TABLE queries_fts USING fts5(
    title,
    description,
    body,
    objects,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER queries_ai AFTER INSERT ON queries BEGIN
    INSERT INTO queries_fts(rowid, title, description, body, objects)
    VALUES (new.id, new.title, new.description, new.body, '');
END;
CREATE TRIGGER queries_ad AFTER DELETE ON queries BEGIN
    INSERT INTO queries_fts(queries_fts, rowid, title, description, body, objects)
    VALUES ('delete', old.id, old.title, old.description, old.body, '');
END;
CREATE TRIGGER queries_au AFTER UPDATE ON queries BEGIN
    INSERT INTO queries_fts(queries_fts, rowid, title, description, body, objects)
    VALUES ('delete', old.id, old.title, old.description, old.body, '');
    INSERT INTO queries_fts(rowid, title, description, body, objects)
    VALUES (new.id, new.title, new.description, new.body, '');
END;
```

Notas:
- `remove_diacritics 2` → "relatorio" encontra "relatório".
- Coluna `objects` preenchida pelo indexador com nomes de `query_objects` daquela query.
- Em Python, `sqlite3.connect()` com SQLite ≥ 3.43 (Python 3.12 já embute uma versão compatível) suporta FTS5 nativamente — sem extensão extra.
- `schema.sql` é lido via `importlib.resources` e aplicado na primeira abertura; versão em `meta('schema_version')` controla migrações incrementais.

**Sintaxe de busca:**

| Entrada | Comportamento |
|---|---|
| `cross apply versão` | FTS5 em title+description+body+objects, ranking `bm25()` |
| `table:Documentos` | filtro NOCASE em `query_objects` (type=table) |
| `col:DataCriacao` | filtro NOCASE em `query_objects` (type=column) |
| `tag:relatorio` | filtro por tag |
| `table:Doc tag:relatorio erros` | interseção dos filtros + FTS no texto livre |

`sqlshelf/core/search.py` faz o parsing da string de busca, separando prefixos `table:`/`col:`/`tag:` do texto livre, e monta a query SQL combinando filtros relacionais com `MATCH`.

---

## 6. Extração de objetos via sqlglot

```python
import sqlglot
from sqlglot import exp

def extract_objects(sql_body: str, dialect: str = "tsql") -> dict[str, set[str]]:
    try:
        tree = sqlglot.parse_one(sql_body, dialect=dialect)
    except sqlglot.ParseError:
        return {"table": set(), "column": set(), "procedure": set(), "function": set()}

    tables = {t.name for t in tree.find_all(exp.Table)}
    columns = {c.name for c in tree.find_all(exp.Column)}
    # CTEs entram em lista de exclusão (não são tabelas físicas)
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    tables -= ctes

    return {"table": tables, "column": columns, "procedure": set(), "function": set()}
```

- `dialect="tsql"` cobre `CROSS APPLY`, CTEs, funções de janela, `TOP`, hints.
- Em caso de `ParseError`: retorna conjuntos vazios, indexador marca `has_parse_error = True` na linha de `queries` (campo adicional opcional) e sinaliza `⚠` na UI — nunca propaga exceção para o usuário.
- Resultado alimenta `query_objects` e a coluna `objects` do FTS.

---

## 7. Backlog por fases

### Fase 1 — MVP
*Critério de pronto: você abandona o Explorer para achar queries.*

1. ✅ **Bootstrap** (entregue): janela, sidebar, lista, editor com highlight, scanner de pasta com frontmatter tolerante.
2. ✅ **Índice SQLite + FTS5**: `index_db.py` — criação do `.sqlshelf/index.db`, aplicação do `schema.sql`, indexação inicial a partir do `scanner.py` (inserção em `queries`, `tags`, `query_tags`).
3. ✅ **Indexação incremental**: comparar `file_mtime`; calcular `content_hash` (SHA256) só se mudou; reindexar apenas o necessário.
4. ✅ **Busca**: `search_bar.py` + `search.py` — sintaxe `table:`/`col:`/`tag:`, resultados com `snippet()` e ranking `bm25()`, populando `query_list.py`.
5. ✅ **Extração de objetos**: `sql_objects.py` (sqlglot) alimentando `query_objects`; `metadata_panel.py` exibe "Esta query usa: tabelas X, Y / colunas Z".
6. ✅ **Edição e salvamento**: tornar o editor editável com toggle; salvar preserva frontmatter (reescreve só os campos alterados via `frontmatter.py`); reindexa o arquivo salvo.
7. ✅ **Nova query ("colar e salvar")**: `new_query_dialog.py` — colar SQL, preencher título/tags/descrição, escolher subpasta, cria o `.sql` com frontmatter.
8. ✅ **Tags na sidebar**: `sidebar.py` lista tags distintas (via `tags`/`query_tags`); clicar filtra a `query_list`.
9. ✅ **Watcher**: `watcher.py` (watchdog) com debounce ~500ms, reindexando arquivos alterados/criados/excluídos fora do app, atualizando a UI via Qt signal.
10. ✅ **Encoding**: `encoding.py` — detecção UTF-8 (com/sem BOM) / UTF-16 / fallback Windows-1252 na leitura; gravação sempre UTF-8.
11. ✅ **Config & projetos recentes**: `config.py` — `~/.sqlshelf/config.json` com lista de projetos recentes; menu para trocar de projeto.
12. ✅ **Ações de sistema**: botões "Open in SSMS", "Reveal Folder", "Copy" via `subprocess`/`os.startfile`.

### Fase 2 — Refinamento
- Favoritos e "abertas recentemente".
- Duplicar query como base para nova demanda; copiar sem frontmatter.
- Snippets/templates parametrizáveis.
- Atalho `Ctrl+P` (paleta de comandos/busca rápida).
- Busca reversa: clicar em tabela → queries que a referenciam.
- Empacotamento com PyInstaller (Windows primeiro) + GitHub Actions.

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
| Frontmatter corrompido por edição externa | Parser tolerante (já no esqueleto): indexa só o body, `has_frontmatter=False`, sinaliza ⚠ |
| `sqlglot` não parsear T-SQL não-padrão | `ParseError` tratado, `has_parse_error=True`, sem exceção ao usuário |
| Escopo crescer antes do MVP rodar | Regra de ouro: nada de Fase 2+ antes de uso real por 1 semana |
| Pastas grandes (milhares de arquivos) | Indexação incremental por `mtime`+hash; `QThread`/`QRunnable` para scan sem travar a UI |
| Performance da UI com listas grandes | `QListWidget` simples até ~1000 itens; migrar para `QListView` + model próprio se necessário |

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

# Lint/format
ruff check .
black .
```

**Extensões VS Code:**
```
ms-python.python
ms-python.vscode-pylance
charliermarsh.ruff
```

---

## 10. Decisões de design da UI

- **Layout:** `QSplitter` horizontal de 3 painéis — sidebar (pastas/tags), lista de queries, editor+metadados — já presente no esqueleto.
- **Tema:** `qt-material`, tema `dark_teal` por padrão; configurável depois.
- **Tipografia:** fonte monoespaçada (Cascadia Code, com fallback `QFont.StyleHint.Monospace`) no editor e nomes de objetos.
- **Feedback de indexação:** status bar inferior ("N queries indexed"), atualizado pelo watcher e pela indexação inicial.
- **Atalhos:** `Ctrl+F` foca busca, `Ctrl+S` salva, `Ctrl+N` nova query, `Ctrl+E` toggle modo edição.
