# Diagrama da consulta

> **Referência visual:** `docs/diagram/*.png` (4 telas). Este documento cobre o que as imagens não mostram: regras de análise, layout, comportamento, o que não pode regredir e a ordem de implementação.
>
> **Escopo:** nova aba **Diagrama** no painel de detalhes, ao lado do SQL, e uma janela expandida. O `CLAUDE.md` continua valendo inteiro: core sem PySide6, nada de I/O na UI thread, arquivos no disco são a fonte da verdade, textos via i18n em EN / PT-BR / ES.

## Telas de referência

| Arquivo | O que mostra |
|---|---|
| `06-diagrama-painel.png` | Aba Diagrama no painel (escuro): SELECT com 4 tabelas, junções INNER/LEFT, filtros nas colunas e o nó Resultado |
| `07-diagrama-expandido.png` | Janela expandida (escuro): script ETL com `GO`, temporária, CTE, MERGE, INSERT e UPDATE; nó selecionado com os demais esmaecidos; aviso de análise parcial; legenda |
| `08-diagrama-claro.png` | Tema claro, script real da biblioteca (`BEGIN/END`, `SELECT INTO #tmp`): seleção de instrução e tooltip da condição de junção |
| `09-diagrama-filtros.png` | Prancha de revisão (não é uma tela do app): SQL de exemplo ao lado do diagrama, com os 5 jeitos de mostrar filtro numerados |

Os textos das imagens estão em PT-BR; no código, as strings de origem continuam em inglês, com tradução nos três locales. As imagens usam Inter e JetBrains Mono no lugar de Segoe UI e Cascadia Mono.

## O que é e o que não é

- **É** uma modelagem 100 % derivada do texto da consulta: quais objetos ela usa, como se ligam (junções, `APPLY`), que filtros sofrem e para onde os dados vão (`INTO`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, CTEs, resultado).
- **Não é** modelagem de schema (Fase 4) nem depende de catálogo (Fase 3). Sem acesso ao banco: sem tipos, sem PK/FK reais, sem cardinalidade. Cada tabela mostra só as colunas que a consulta cita. A coluna da direita diz *como* cada uma é usada: o papel (`join`, `select`…) ou, quando é filtrada, a própria condição (`>= @inicio`). Quando o catálogo da Fase 3 existir, o tipo pode entrar ali.
- **Sem dependência nova.** `QGraphicsView`/`QGraphicsScene` (QtWidgets) e `QtSvg` já vêm no PySide6. Nada de QtWebEngine, JS ou segundo processo.
- **Sem mudança** no schema do índice nem no formato dos `.sql`. O grafo é calculado sob demanda e guardado em cache só em memória. Posições arrastadas pelo usuário não são gravadas em lugar nenhum.

## Arquitetura

| Módulo | Camada | Responsabilidade |
|---|---|---|
| `sqlshelf/core/sql_segments.py` | core | Lotes por `GO` e segmentação do T-SQL procedural em instruções DML |
| `sqlshelf/core/query_graph.py` | core | sqlglot → `QueryGraph` (nós, arestas, instruções, avisos). Nunca lança exceção |
| `sqlshelf/core/graph_layout.py` | core | Grupos, camadas, ordem e coordenadas. Recebe os tamanhos dos nós prontos |
| `sqlshelf/ui/diagram_view.py` | ui | `QGraphicsView` + itens (nó, moldura, aresta, rótulo), tema, interação |
| `sqlshelf/ui/diagram_window.py` | ui | Janela expandida (mesmo processo, não modal) |
| `sqlshelf/ui/editor_strip.py` | ui | Abas **SQL \| Diagrama** e ferramentas contextuais |
| `sqlshelf/ui/tokens.py` | ui | Cores do diagrama e `diagram_group_color()` (puro) |
| `sqlshelf/ui/main_window.py` | ui | `QStackedWidget(editor, diagrama)`, worker, cache, sinais |

**Fluxo:** consulta selecionada → se a aba Diagrama estiver visível → `_DiagramWorker(QRunnable)` roda `build_query_graph(body)` → sinal com o grafo → a UI mede os nós com `QFontMetrics`, chama `layout()` e monta a cena.

- A análise (sqlglot) roda no worker. O layout roda na UI thread: é cálculo puro e barato (meta: < 10 ms para 50 nós; medir em teste).
- Contador de geração descarta resultados velhos, igual aos outros workers do `main_window.py`.
- Cache LRU de 32 grafos, chave `(sha256 do body, dialeto)`.
- Nada é analisado enquanto a aba SQL estiver ativa.
- O dialeto segue o de `sql_objects.py`: `tsql`, ou `bigquery` quando há crase. Extrair `_pick_dialect` para uso comum.

## Modelo — `query_graph.py`

```python
NodeKind = Literal["table", "temp", "cte", "derived", "result"]
EdgeKind = Literal["join", "flow"]

@dataclass(frozen=True)
class Span:              # offsets no body (sem frontmatter); end exclusivo
    start: int
    end: int

@dataclass
class FilterInfo:
    clause: Literal["where", "on", "having"]
    predicate: str           # forma curta, sem a coluna: ">= @inicio", "NOT IN ('C', 'D')", "YEAR(…) = 2026"
    full: str                # condição inteira de onde veio (OR completo, até 300 caracteres)
    in_or: bool              # parte de uma condição com OR
    join_label: str | None   # só para clause="on": "INNER", "LEFT"…
    statement: int
    span: Span

@dataclass
class GraphColumn:
    name: str
    roles: set[str]          # join on set insert into where group_by having order_by select apply
    is_key: bool             # usada em condição de junção
    spans: list[Span]
    expression: str | None   # só no nó Resultado: "COUNT(p.PedidoID)"
    filters: list[FilterInfo]

@dataclass
class GraphNode:
    id: str                  # ver "Identidade"
    kind: NodeKind
    schema: str | None       # "dbo", "db.dbo", "srv.db.dbo"
    name: str                # como exibido: "Pedidos", "#pedidos_validos", "@t", "T"
    aliases: list[str]
    columns: list[GraphColumn]
    statements: set[int]
    writes: list[str]        # "INTO", "INSERT", "UPDATE", "DELETE", "MERGE"
    is_base: bool            # primeira fonte do FROM da instrução → selo FROM
    spans: list[Span]
    filters: list[FilterInfo]  # filtros que não cabem numa coluna: HAVING (rodapé)

@dataclass
class GraphEdge:
    kind: EdgeKind
    source: str
    target: str
    source_column: str | None
    target_column: str | None
    label: str               # "INNER", "LEFT", "CROSS APPLY", "BETWEEN", "WHERE", "INTO"…
    condition: str           # texto da condição (tooltip), até 200 caracteres
    optional_side: Literal["source", "target", "both"] | None
    statement: int
    span: Span | None

@dataclass
class StatementInfo:
    index: int               # 1, 2, 3… na ordem do arquivo
    kind: str                # SELECT, INSERT, UPDATE, DELETE, MERGE
    summary: str             # "SELECT … INTO #SystemHealthSessionData"
    line: int
    span: Span
    status: Literal["ok", "approximate", "skipped"]

@dataclass
class QueryGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    statements: list[StatementInfo]
    notes: list[str]         # chaves i18n: "dynamic_sql", "too_large"…
    def for_statement(self, index: int) -> QueryGraph: ...
```

### Identidade dos nós

- **Tabela:** `casefold` do nome qualificado. Um nome sem schema funde com a versão qualificada só quando ela é única no script.
- **Mesma tabela duas vezes na mesma instrução** (auto-junção): um nó por alias, exibidos como `Funcionario e` e `Funcionario m`. Entre instruções, a mesma tabela é o mesmo nó. É isso que liga a linhagem de um script.
- **Temporária e variável de tabela:** `#nome`, `##nome`, `@nome`. Atenção: o sqlglot tira o `#` de `.name`; detectar pelas flags `temporary` / `global_` do `Identifier` e exibir com `.sql(dialect)`. `@t` vem como `exp.Parameter`.
- **CTE:** `cte:<instrução>:<nome>`. CTEs com o mesmo nome em instruções diferentes são nós diferentes.
- **Derivada** (subconsulta no FROM, `APPLY`, função de tabela, `.nodes()`): `derived:<instrução>:<alias>`; sem alias, `(subconsulta)`.
- **Resultado:** `result:<instrução>`. Só para SELECT que devolve linhas: não `INTO`, não `SELECT @x = …`.

## Análise — regras

### 1. Lotes e instruções (`sql_segments.py`)

Verificado com sqlglot 30.x:
- `GO` **não** é separador: o tokenizer trata `GO` como comando que engole o resto do script.
- `CREATE PROCEDURE … AS BEGIN … END` engole o corpo; o `INSERT` de dentro some.
- `IF … BEGIN … END ELSE …` vira uma árvore sem sentido.

Por isso, antes do parse:

1. Separar lotes por linhas `GO` (com contagem opcional e comentário), guardando os offsets.
2. Em cada lote, tokenizar com o tokenizer `tsql` e recortar as instruções DML com uma máquina de estados:
   - Profundidade de parênteses e de `CASE … END`.
   - Inicia em `SELECT/WITH/INSERT/UPDATE/DELETE/MERGE` na profundidade 0.
   - Termina em `;` ou em palavra de controle (`DECLARE SET IF ELSE WHILE BEGIN END RETURN EXEC DROP CREATE ALTER…`).
   - Continuações válidas: `UNION/EXCEPT/INTERSECT SELECT`; `WITH … <DML principal>`; `INSERT … SELECT/EXEC`; `UPDATE … SET`; `MERGE … THEN UPDATE/INSERT/DELETE`.
   - `WITH` só abre instrução como `WITH nome AS (` ou `WITH nome (cols) AS (`. `WITH (NOLOCK)`, `WITH CHECK` e `WITH COMPRESSION` não abrem.
   - `UPDATE STATISTICS` não é DML.
3. A condição de `IF`/`WHILE` e o `SELECT` dentro de `DECLARE @x = (…)` são ignorados.
4. Lote que o tokenizer rejeita (string ou comentário sem fechar) → instrução `skipped`, os outros lotes seguem.

O apêndice tem um **protótipo validado** com os casos de teste abaixo. Use como ponto de partida e adapte às convenções do projeto.

### 2. Parse de cada instrução

- `sqlglot.parse_one(texto, dialect, error_level=RAISE)`. Se falhar, tenta `IGNORE`: se a árvore for do tipo esperado (`Select`, `Insert`…), usa e marca `approximate`; senão, `skipped`.
- `approximate` e `skipped` alimentam o aviso "Análise parcial", com a linha.
- Casos que falham no modo estrito hoje: `INSERT … EXEC`, `OUTPUT … INTO`, `WITH XMLNAMESPACES`.
- **SQL dinâmico:** `EXEC(@sql)`, `EXEC sp_executesql` ou `EXECUTE (` no lote → nota `dynamic_sql`. O conteúdo não é analisado.
- Silenciar o stderr do sqlglot como `sql_objects.py` já faz. `build_query_graph` envolve tudo em `try/except` e devolve grafo vazio com nota em vez de lançar.

### 3. Fontes, aliases e colunas

- Usar `sqlglot.optimizer.scope.traverse_scope`. `scope.sources` mapeia alias → `Table` ou `Scope` (CTE, derivada).
- Coluna qualificada (`c.table`) → fonte pelo alias do escopo, inclusive alias de escopo externo em subconsulta correlacionada.
- Coluna sem qualificador → a única fonte do escopo; com várias fontes, fica sem dono (sem erro).
- **Lacunas do `traverse_scope`, verificadas:** as colunas do `SET` de `UPDATE` e as do `ON`/`WHEN` de `MERGE` não aparecem em `scope.columns`. Percorrer `Update.expressions` e os args de `Merge` à parte, resolvendo pelo mapa de aliases da instrução.
- `UPDATE t SET … FROM mart.X t` e `DELETE d FROM dbo.T d …`: o alvo vem como `Table("t")`. Resolver pelo alias, como `sql_objects.py` já faz. Nunca criar nó `t`.
- **Papel** de cada coluna pelo ancestral:

| Onde aparece | Papel |
|---|---|
| `Join.on` | `join` |
| `ON` do `MERGE` | `on` |
| `SET` (lado esquerdo) | `set` |
| Lista de colunas do `INSERT` | `insert` |
| Colunas criadas por `SELECT INTO` | `into` |
| `Where` | `where` |
| `Group` | `group by` |
| `Having` | `having` |
| `Order` | `order by` |
| Lista do `SELECT` | `select` |
| Argumento de `APPLY` | `apply` |

- **Posição:** `Identifier.meta` traz `start`/`end` (end inclusivo) relativos ao texto parseado. Somar o offset da instrução para obter `Span` no body; é o que o duplo clique usa.

### 4. Arestas de junção (`join`)

- Para cada `exp.Join` com `on`: cada `EQ` entre colunas de **nós diferentes** → uma aresta coluna↔coluna. Chave composta = várias arestas; a UI mostra o rótulo só na primeira.
- **Rótulo** a partir de `side`/`kind`:

| Junção | Rótulo | `optional_side` |
|---|---|---|
| `JOIN` puro (sem side/kind) ou `INNER` | `INNER` | — |
| `LEFT` | `LEFT` | `target` |
| `RIGHT` | `RIGHT` | `source` |
| `FULL` | `FULL` | `both` |
| `CROSS` | `CROSS` | — |
| `exp.Lateral` com `cross_apply=True` | `CROSS APPLY` | — |
| `exp.Lateral` com `cross_apply=False` | `OUTER APPLY` | `target` |

- **Comparação não-igual** entre colunas de dois nós (`BETWEEN`, `<`, `>`, `<>`, `LIKE`) → uma aresta por par de nós, com o operador como rótulo.
- **Coluna comparada com literal ou variável** dentro do `ON` → só papel `join`, sem aresta.
- **Junção implícita** (`FROM a, b WHERE a.x = b.x`) → rótulo `WHERE`.
- **`APPLY` de função sobre coluna** (`a.XMLDATA.nodes(…) T(C)`, `dbo.fn(a.id) f`) → aresta da coluna de origem para o nó derivado, sem coluna de destino.
- **Subconsulta em `WHERE`:**
  - `x IN (SELECT y FROM t)` → aresta `x`↔`t.y`, rótulo `IN`.
  - `EXISTS`/`NOT EXISTS` correlacionado → aresta pela condição de correlação, rótulo `EXISTS`/`NOT EXISTS`.
- `condition` = `on.sql(dialect)` (até 200 caracteres) para o tooltip.

### 5. Arestas de fluxo (`flow`)

| Instrução | Fluxo | Rótulo |
|---|---|---|
| `SELECT … INTO X` | cada fonte do FROM/JOIN → X | `INTO` |
| `INSERT INTO X SELECT …` | fontes → X | `INSERT` |
| `INSERT INTO X VALUES …` / `… EXEC` | nenhum; X ganha `writes=INSERT` | — |
| `UPDATE X … FROM …` | demais fontes → X | `UPDATE` |
| `DELETE X FROM X JOIN Y` | Y → X | `DELETE` |
| `MERGE X USING Y ON …` | Y → X (o `ON` vai para `condition`; sem aresta de junção) | `MERGE` |
| CTE / derivada | fontes do corpo → nó; nó → quem o consome | — |
| SELECT que devolve linhas | fontes do FROM/JOIN → `result:<n>` | — |

- Em `UNION`, as fontes de todos os ramos alimentam o mesmo destino.
- O nó Resultado lista as colunas de saída (alias ou nome) com a expressão em `expression`.

### 6. Filtros

**De onde vêm:** `WHERE` de `SELECT`, `UPDATE` e `DELETE`; `HAVING`; e as partes do `ON` que não ligam duas tabelas. As condições de `WHEN MATCHED AND …` do `MERGE` ficam fora por enquanto.

**Como separar:**
1. Quebrar a condição nos termos do `AND` (`exp.And.flatten()`), desembrulhando `exp.Paren`.
2. Para cada termo, olhar as colunas citadas:

| O termo… | Vira |
|---|---|
| liga colunas de dois nós por igualdade ou comparação | aresta de junção (regra 4), não filtro |
| tem subconsulta (`IN (SELECT…)`, `EXISTS`) | aresta `IN`/`EXISTS` (regra 4), não filtro |
| cita colunas de um nó só, comparadas com literal, variável, parâmetro ou expressão sem colunas de outro nó | filtro em cada coluna citada |
| é um `OR` (mesmo entre colunas de nós diferentes) | filtro em cada coluna, com o pedaço do `OR` que a cita como `predicate`, o `OR` inteiro em `full` e `in_or=True` |

3. Filtro do `ON` → `clause="on"` e `join_label` da junção. Filtro do `HAVING` → `GraphNode.filters` do nó Resultado; sem Resultado (`INSERT … SELECT … HAVING`), vai para o nó de destino.

**Forma curta (`predicate`):** montar a partir da árvore, não de `.sql()`. Verificado: o sqlglot escreve `NOT p.Status IN ('C', 'D')` e `NOT p.Obs IS NULL`, que ficam estranhos na tela.

| SQL | `predicate` |
|---|---|
| `p.DataPedido >= @inicio` | `>= @inicio` |
| `@x <= p.Valor` (coluna à direita) | `>= @x` (inverte o operador) |
| `p.Status NOT IN ('C', 'D')` | `NOT IN ('C', 'D')` |
| `p.T IN (1, 2, 3, 4, 5)` | `IN (1, 2, 3, …)` (até 3 itens) |
| `p.Obs IS NOT NULL` | `IS NOT NULL` |
| `p.Cod LIKE 'A%'` | `LIKE 'A%'` |
| `p.N BETWEEN 1 AND 5` | `BETWEEN 1 AND 5` |
| `YEAR(p.DataPedido) = 2026` | `YEAR(…) = 2026` |
| `(p.Status = 'A' OR c.Tipo = 'X')` | `= 'A'` em `p.Status` e `= 'X'` em `c.Tipo`, ambos `in_or` |
| qualquer outra forma | o termo inteiro, truncado |

Os filtros também somam o papel `where`, `on` ou `having` à coluna, como antes.

## Layout — `graph_layout.py`

`layout(graph, sizes: dict[str, tuple[float, float]], direction="LR") -> Layout` com `nodes: dict[id, Rect]`, `groups: list[GroupBox]`, `routes: dict[int, list[Point]]` e `size`. Puro, determinístico (ordenar por chaves estáveis) e sem Qt.

**Grupos (molduras)**

| Nó | Grupo | Título |
|---|---|---|
| `table` com schema | um por schema | `vendas` + "schema" em tom fraco |
| `table` sem schema | um só | "Tabelas" |
| `temp` | um só | "Temporárias" |
| `cte`, `derived` | um só | "CTEs e derivadas" |
| `result` | um só | "Resultado" |

**Camadas** (por grupo, não por nó; é o que dá o visual das imagens):
1. Grafo de grupos com as arestas de fluxo (origem → destino), ignorando laços dentro do mesmo grupo.
2. Somar as junções entre grupos orientadas a partir do grupo do nó-base da instrução (BFS para fora). Cada uma entra só se não criar ciclo.
3. Ciclos restantes: remover arestas de retorno (DFS).
4. Camada = caminho mais longo.
5. Grupos que só têm saídas descem para `min(camada dos sucessores) − 1`.

**Ordem**
- Na camada, grupos empilhados e ordenados pelo baricentro dos vizinhos (2 varreduras ida e volta).
- No grupo, nós empilhados por baricentro, com o nó-base como desempate.
- Mais de 6 nós no grupo → subcolunas de até 6 dentro da mesma moldura.

**Arestas**
- **Na mesma camada:** junção sai pela direita dos nós em "colchete", com faixas de 10 px; fluxo sai pela esquerda.
- **Que atravessam mais de uma camada:** pontos intermediários no vão vertical mais próximo da reta, em cada camada do caminho, para nunca cortar um nó. É o que a tela 07 mostra.

**Medidas:** vão entre camadas 80 px (mais as faixas), entre grupos 26, entre nós 18, padding da moldura 14, título da moldura 28.

## Visual

**Tokens novos em `tokens.py`** (as duas paletas com as mesmas chaves; `test_theme` já cobre isso):

| Token | Escuro | Claro |
|---|---|---|
| `dg_canvas` | `#15171a` | `#f4f5f7` |
| `dg_dot` (grade de pontos, 18 px) | `#262a30` | `#dde1e6` |
| `dg_node` | `#1f2228` | `#ffffff` |
| `dg_row_key` | `#23272e` | `#f6f7f9` |
| `dg_ink` (texto sobre cabeçalho colorido) | `#0f1114` | `#121417` |
| `dg_join` | `#9aa2ab` | `#6b737c` |
| `dg_flow` | `#5aa7ff` | `#2f74d6` |
| `dg_dim` (opacidade) | `.28` | `.30` |
| `dg_frame_fill` / `dg_frame_border` (alpha da cor do grupo) | `.07` / `.38` | `.10` / `.55` |

**Cor do grupo:** `diagram_group_color(kind, key)`.
- Fixas: temporárias `#f5a524`, CTEs e derivadas `#b38bff`, Resultado `#2dd4bf`.
- Schemas: paleta `#4c9aff #ff7eb6 #ff8b6b #8fa3bf`; `dbo` sempre `#4c9aff`, demais por `zlib.crc32(casefold) % 4`.
- O verde de destaque nunca é cor de grupo: ele fica para seleção e foco, como no resto do app.

**Nó**
- Cabeçalho de 30 px na cor do grupo, nome semibold em `dg_ink`, alias em mono com 60 % de opacidade.
- Selos à direita: `FROM`, `INTO`/`INSERT`/`UPDATE`/`DELETE`/`MERGE` (juntos com " · "), `CTE`, `APPLY`, `USING`, "N colunas".
- Linhas de 26 px. Colunas de junção primeiro, em semibold, com ícone de elo e fundo `dg_row_key`; depois, pela ordem em que aparecem.
- À direita, em mono 11 px `text3`, o papel mais relevante: `join > on > set > insert > into > where > group by > having > order by > select > apply`. O tooltip lista todos, com as linhas.
- **Coluna filtrada** (tela 09): a direita mostra o filtro no lugar do papel, mesmo em coluna de junção (o ícone de elo continua à esquerda).
  - Ícone de funil de 12 px em `text3`, depois o `predicate` em mono 11,5 px `text2`, com as cores de sintaxe do editor (`syn_keyword`, `syn_string`, `syn_number`, `syn_function`, `syn_operator`).
  - Filtro vindo do `ON`: uma marca `on` antes do funil (mono 10,5 px `text3`, borda 1 px `border_strong`, raio 3).
  - Dois filtros ou mais na mesma coluna: o primeiro e "+N".
  - Texto longo é elidido; o tooltip mostra tudo.
- **Contagem no cabeçalho:** selo com funil e o número de filtros do nó (colunas + rodapé), depois dos outros selos.
- **Rodapé `HAVING`:** última linha do nó, com borda superior tracejada em `border_strong` e fundo `dg_row_key`; "HAVING" em mono 11 px `text3` à esquerda e o filtro à direita, no mesmo formato das colunas.
- No Resultado, a direita mostra a expressão, elidida.
- Até 8 linhas, depois "+N colunas" (clique expande).
- Largura igual dentro do grupo, entre 168 e 270 px; nomes longos elididos, com tooltip.

**Moldura:** raio 10, fundo e borda na cor do grupo com os alphas acima, barra de título de 28 px em cor cheia com ▾, nome e tipo.

**Arestas**
- **Junção:** linha contínua de 1,5 px em `dg_join`. Ponto cheio nas pontas; círculo vazado no lado opcional.
- **Fluxo:** tracejado 6/4 em `dg_flow`, opacidade .85, com seta.
- **Rótulo:** pílula mono de 11 px no meio da aresta. Nos colchetes, vai para o vão entre nós mais próximo do meio, nunca em cima de um nó.
- **Fluxo para o Resultado de um SELECT:** a UI agrupa as arestas por moldura de origem (uma saindo de cada moldura), para não cruzar a tela (tela 06).

**Seleção e hover**
- Nó selecionado: borda de 2 px em `accent` com halo.
- Arestas do nó selecionado ficam em `accent_text` (2 px; fluxo 2,25 px); nós e arestas não ligados vão para opacidade `dg_dim`. As molduras não esmaecem (tela 07).
- Linha em hover: fundo `hover` e barra de 2 px em `accent` à esquerda.

## Comportamento

### Aba e barra
- A faixa do editor (`EditorStrip`) vira abas **SQL | Diagrama**, com sublinhado `accent` na ativa.
- `Ctrl+Shift+D` alterna as abas (livre hoje: `Ctrl+D` já é usado).
- A aba escolhida fica salva em `config.json` (`details_tab`).
- **Com o Diagrama ativo:**
  - À esquerda: "N tabelas · M junções · K filtros" ("N objetos" quando houver temporárias ou CTEs; cada parte some quando é zero).
  - À direita: seletor de instrução (só com mais de uma: "Todas (N)", "1 · SELECT … INTO #x", …), Ajustar, −, zoom %, +, ⋯ e Expandir.
  - O ⋯ tem: Reorganizar, Mostrar resultado (marcado), Exportar PNG…, Exportar SVG…, Copiar imagem.
- Zoom inicial = ajustar à área, sem passar de 100 %. Ao trocar de consulta, volta a ajustar.

### Mouse e teclado
- **Rolar:** roda do mouse. **Zoom no cursor:** `Ctrl`+roda (25–200 %).
- **Mover a vista:** arrastar o fundo.
- **Arrastar nó:** move o nó e a moldura cresce junto. Arrastar o título move o grupo inteiro. ▾ recolhe o grupo, e as arestas passam a ligar na moldura.
- **Clique:** no nó seleciona; no fundo limpa a seleção.
- **Hover em aresta:** tooltip com "INNER JOIN · instrução 1 · linha 27", a condição em bloco de código e "Duplo clique abre no SQL" (tela 08).
- **Hover em coluna filtrada ou no rodapé `HAVING`:** tooltip com "Filtro · WHERE · linha 9", o filtro completo em bloco de código e "Duplo clique abre no SQL" (tela 09). Com vários filtros, um bloco por filtro. Acrescentar uma linha quando:
  - `in_or`: "Parte de uma condição com OR", e o bloco mostra o `full`;
  - `clause="on"` numa junção externa: "No ON do LEFT JOIN: não remove linhas de <tabela do outro lado>".
- **Duplo clique** em nó, coluna ou aresta: vai para a aba SQL e seleciona exatamente o `Span`, reaproveitando a navegação do editor.
- **Botão direito no nó:** Ir para o SQL, Buscar em todas as consultas (só tabela; usa `filter_requested("table", nome)`), Copiar nome.
- **Botão direito no fundo:** Ajustar, Reorganizar, Exportar.
- **Com o diagrama em foco:** `Ctrl+0` ajusta, `Ctrl+=` / `Ctrl+-` aproximam e afastam, `Esc` limpa a seleção.

### Edição
- Em modo edição (`Ctrl+E`) com o Diagrama visível, reanalisar 800 ms após a última tecla, mantendo as posições arrastadas por id de nó.
- Se o texto não parsear no meio da digitação, manter o último grafo bom e mostrar "desatualizado" na faixa.

### Janela expandida (tela 07)
- Uma instância só, não modal. Título "Diagrama — <arquivo>". Segue a consulta selecionada na janela principal. Geometria salva em `config.json`. `Esc` fecha.
- Barra: título e caminho da consulta, seletor de instrução, Reorganizar, Ajustar, zoom, Exportar ▾, Ir para o SQL (traz a janela principal com o SQL ativo) e ⋯.
- Legenda no canto inferior esquerdo, que pode ser ocultada pelo ⋯.
- Barra de status: contagens à esquerda; seleção e conexões à direita.

### Exportar
- PNG em 2x com o fundo do canvas; SVG via `QSvgGenerator`; Copiar imagem vai para a área de transferência.
- Arquivo só sai por `QFileDialog`, com o nome `<arquivo>.diagram.png` e a pasta da consulta como sugestão. Nada é gravado sem o usuário escolher.

### Estados
| Situação | O que aparece |
|---|---|
| Analisando (> 150 ms) | "Analisando…" centralizado, em `text3` |
| Nada a desenhar | Ícone, "Nenhuma tabela encontrada nesta consulta" e "O diagrama mostra SELECT, INSERT, UPDATE, DELETE e MERGE." |
| Instruções `skipped`/`approximate` | Aviso no topo: "Análise parcial — N instruções não puderam ser lidas por completo (linha L)", com "Ir para a linha" e ✕ (some só para aquela consulta) |
| SQL dinâmico | Aviso informativo: "SQL dinâmico (EXEC / sp_executesql) não é analisado." |
| Mais de 60 nós | Colunas recolhidas (só cabeçalhos) e aviso com "Mostrar colunas" |
| Body > 400 KB | Não analisa sozinho: "Arquivo grande para análise automática" e "Analisar mesmo assim" |
| Falha total | "Não foi possível analisar esta consulta." e a primeira linha do erro, em `text3` |

- Troca de tema e de idioma ao vivo atualizam o diagrama sem refazer a análise.
- Palavras SQL (`INNER`, `LEFT`, `INTO`…) e papéis (`join`, `where`…) não são traduzidos.

## Não pode regredir

- [ ] Editor: highlight, números de linha, linha atual, destaque de ocorrências, regra de >1000 linhas, carga assíncrona
- [ ] `Ctrl+E`, `Ctrl+S`, `Esc` na edição; `Ctrl+A` e Copiar SQL agem sobre o SQL mesmo com o Diagrama ativo
- [ ] Chips de tabela e navegação para a ocorrência
- [ ] Navegação rápida pela lista não trava (estender `test_ui_responsiveness.py` com a aba Diagrama ativa)
- [ ] Todos os atalhos do README; troca de tema e idioma ao vivo
- [ ] Nada de I/O nem sqlglot na UI thread; conexões SQLite inalteradas
- [ ] `sql_objects.py` e o índice continuam iguais (o diagrama não grava nada)

## Fatias

Uma por vez, na branch `feat/query-diagram`, um commit por fatia (Conventional Commits). Cada fatia termina com:
- testes `pytest` passando;
- `ruff`, `black` e `mypy` limpos;
- entrada no `CHANGELOG.md` em `[Unreleased]`;
- a caixa marcada aqui.

### D1 — Segmentação (core)
- [x] **Entrega:** `sql_segments.py` com `split_batches()`, `segments()` e `split_statements()`, offsets exatos.
- **Testes** (`test_sql_segments.py`): os casos de 1 a 6 da tabela abaixo; `GO` com contagem e comentário; `CASE … END` e `ELSE` de `CASE`; `WITH (NOLOCK)` e `WITH CHECK`; `UPDATE STATISTICS`; `body[s.start:s.end] == s.text` em todos.

### D2 — Grafo (core)
- [ ] **Entrega:** `query_graph.py` completo (modelo, regras 2 a 6, `for_statement`).
- **Testes** (`test_query_graph.py`): todos os casos da tabela abaixo, com `pytest.mark.parametrize`; os de filtro (17 a 20) conferem `predicate`, `clause`, `in_or` e `span` de cada filtro.

### D3 — Layout (core)
- [ ] **Entrega:** `graph_layout.py`.
- **Testes** (`test_graph_layout.py`):
  - nenhum nó sobrepõe outro;
  - nenhuma moldura cobre nó de outro grupo;
  - fluxo vai da camada menor para a maior;
  - rotas de arestas longas não cruzam nós;
  - mesma entrada, mesma saída;
  - 50 nós em < 10 ms.

### D4 — Aba e desenho (ui)
- [ ] **Entrega:**
  - tokens e `diagram_group_color`;
  - `diagram_view.py` desenhando molduras, nós, arestas e rótulos;
  - filtros nas linhas (funil, marca `on`, "+N"), contagem no cabeçalho, rodapé `HAVING` e "K filtros" na faixa;
  - abas no `EditorStrip` e `Ctrl+Shift+D`;
  - worker, cache, contador de geração;
  - Ajustar, zoom e pan;
  - estados Analisando, Vazio e Falha.
- **Aceite:** telas 06 e 08 sem tooltip, e os itens 1 a 4 da tela 09; troca de tema ao vivo.

### D5 — Interação (ui)
- [ ] **Entrega:**
  - seleção com esmaecimento;
  - hover e tooltip (arestas, colunas filtradas e rodapé `HAVING`, item 5 da tela 09);
  - duplo clique → SQL no `Span`;
  - menus de contexto;
  - arrastar nó e grupo, recolher grupo, Reorganizar;
  - seletor de instrução;
  - avisos (parcial, dinâmico, grande);
  - reanálise durante a edição.
- **Testes:** duplo clique seleciona o trecho certo; trocar de consulta descarta resultado velho; a seleção de instrução filtra nós e arestas.

### D6 — Janela expandida e exportação (ui)
- [ ] **Entrega:**
  - `diagram_window.py`, com legenda e barra de status;
  - Exportar PNG/SVG e Copiar imagem;
  - README (Features e tabela de atalhos) e CHANGELOG.
- **Aceite:** tela 07.

## Casos de teste

| # | Entrada | O que conferir |
|---|---|---|
| 1 | SELECT da tela 06 (`vendas.Pedidos p` + `Clientes c` INNER + `Pagamentos pg` LEFT + `dbo.Regiao r` LEFT, `WHERE`, `GROUP BY`) | 4 tabelas + resultado; arestas `p.ClienteID–c.ClienteID INNER`, `p.PedidoID–pg.PedidoID LEFT` (opcional `target`), `c.RegiaoID–r.RegiaoID LEFT`; filtros `p.DataPedido >= @inicio` e `p.Status <> 'C'` (`where`); base = Pedidos; resultado com 5 colunas e expressões |
| 2 | Script real da tela 08 (`SET NOCOUNT`, `BEGIN`, `DECLARE`, `SELECT TOP 1 … INTO #SystemHealthSessionData … JOIN … ON (…)`, `;WITH CTE_HealthSession (EventXML) AS (… CROSS APPLY a.XMLDATA.nodes(…) T(C))`, `SELECT … FROM CTE`, `DROP`, `END`) | 2 instruções `ok`; `INNER` entre `xe.address` e `xet.event_session_address`; fluxos `INTO` de xet e xe; nó `#SystemHealthSessionData` com `#`; derivada `T` com `CROSS APPLY` vindo de `XMLDATA`; CTE → resultado |
| 3 | ETL da tela 07, com `GO` entre instruções | 4 instruções; `#pedidos_validos` é o mesmo nó nas instruções 1 e 3; `MERGE` stg.Clientes → dim.Cliente sem aresta de junção; `UPDATE t` resolve para `mart.ClienteLtv` (nenhum nó `t`); `BETWEEN` entre `t.ValorVida` e `f.Minimo` |
| 4 | `CREATE PROCEDURE … BEGIN … BEGIN TRY INSERT … SELECT … FROM #t t LEFT JOIN dbo.Fact f … END TRY BEGIN CATCH THROW; END CATCH END`, com `CASE … ELSE … END` no SELECT | 2 instruções; `dbo.Fact` é um nó só (destino e fonte); `CASE` não corta a instrução |
| 5 | `IF EXISTS (SELECT …) BEGIN UPDATE … END ELSE SELECT …` | 2 instruções; a condição do `IF` é ignorada |
| 6 | Arquivo de DBA: `ALTER TABLE … WITH CHECK`, `BACKUP … WITH COMPRESSION`, `UPDATE STATISTICS`, `DECLARE c CURSOR FOR SELECT …`, `SELECT … WITH (NOLOCK) … OPTION (RECOMPILE)` | só os 2 SELECT viram instruções; nenhum aviso de análise parcial |
| 7 | `FROM t1 a, t2 b WHERE a.id = b.id` | aresta com rótulo `WHERE` |
| 8 | `dbo.Funcionario e LEFT JOIN dbo.Funcionario m ON m.id = e.gerente_id` | dois nós |
| 9 | `WHERE EXISTS (SELECT 1 FROM dbo.O o WHERE o.cid = c.id) AND c.r IN (SELECT r.id FROM dbo.R r)` | arestas `EXISTS` e `IN` |
| 10 | `OUTER APPLY (SELECT TOP 1 …) rv` e `CROSS APPLY dbo.fn(a.id) f` | derivadas com `OUTER APPLY` (opcional) e `CROSS APPLY` |
| 11 | `INSERT INTO ##g SELECT * FROM @t v JOIN [Order Details] od ON …` | nós `##g`, `@t`, `Order Details`; `*` vira linha "* (todas)" |
| 12 | `EXEC(@sql)` e `EXEC sp_executesql @sql` | nota `dynamic_sql`, nenhum nó |
| 13 | Uma instrução válida + `INSERT INTO #r EXEC dbo.usp …` + uma inválida | as válidas intactas; `INSERT … EXEC` `approximate` com nó `#r`; a inválida `skipped` com a linha certa |
| 14 | Colunas sem qualificador, com uma fonte e com várias | atribuídas no primeiro caso; no segundo, ignoradas sem erro |
| 15 | Identificadores com crase | dialeto `bigquery`, como em `sql_objects.py` |
| 16 | Vazio, só comentários, só DDL, string sem fechar, lixo aleatório | grafo vazio; nunca lança exceção |
| 17 | SQL da tela 09 (`ON … AND c.Ativo = 1`, `WHERE p.DataPedido >= @inicio AND p.Status NOT IN ('C', 'D')`, `HAVING SUM(pg.Valor) > 1000`) | 4 filtros: `>= @inicio` e `NOT IN ('C', 'D')` em Pedidos (`where`); `= 1` em `c.Ativo` (`on`, `join_label="INNER"`); `SUM(pg.Valor) > 1000` em `filters` do Resultado (`having`). `c.Ativo` não vira aresta |
| 18 | Uma condição de cada forma da tabela "Forma curta" (`@x <= p.Valor`, `IN` com 5 itens, `IS NOT NULL`, `LIKE`, `BETWEEN`, `YEAR(…) = 2026`) | `predicate` exatamente como na tabela; o operador invertido quando a coluna está à direita |
| 19 | `WHERE (p.Status = 'A' OR c.Tipo = 'X') AND p.Z <> 0` | `= 'A'` e `= 'X'` com `in_or=True` e o mesmo `full`; `<> 0` com `in_or=False`; nenhuma aresta entre p e c por causa do OR |
| 20 | `UPDATE t SET … FROM mart.X t WHERE t.Ativo = 1` e `DELETE d FROM dbo.T d WHERE d.Data < @corte` | filtros nas colunas do alvo resolvido (`mart.X`, `dbo.T`), nunca num nó `t` ou `d` |

## Apêndice — protótipo de `sql_segments.py`

Validado com sqlglot 30.24 nos casos 1–6 e 13. Ajustar tipagem e estilo às convenções do projeto.

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlglot.dialects.dialect import Dialect

_GO_LINE = re.compile(r"(?im)^[ \t]*GO(?:[ \t]+\d+)?[ \t]*(?:--[^\n]*)?$")

DML_START = {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE"}
STOP = {
    "DECLARE", "SET", "IF", "ELSE", "WHILE", "BEGIN", "END", "RETURN", "PRINT",
    "EXEC", "EXECUTE", "DROP", "CREATE", "ALTER", "GO", "RAISERROR", "THROW",
    "COMMIT", "ROLLBACK", "TRUNCATE", "USE", "OPEN", "CLOSE", "FETCH",
    "DEALLOCATE", "BREAK", "CONTINUE", "WAITFOR", "BACKUP", "RESTORE", "DBCC",
    "GRANT", "REVOKE", "DENY", "CHECKPOINT", "KILL", "RECONFIGURE", "BULK",
}


@dataclass(frozen=True)
class Segment:
    kind: str  # SELECT, WITH, INSERT, UPDATE, DELETE, MERGE
    start: int  # offset no body
    end: int  # exclusivo
    text: str


def split_batches(sql: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for m in _GO_LINE.finditer(sql):
        out.append((pos, sql[pos : m.start()]))
        pos = m.end()
    out.append((pos, sql[pos:]))
    return [(o, t) for o, t in out if t.strip()]


def _is_cte_with(toks, i: int) -> bool:
    def w(j: int) -> str:
        return toks[j].text.upper() if j < len(toks) else ""

    if w(i + 1) in ("", "(") or w(i + 1) == "XMLNAMESPACES":
        return w(i + 1) == "XMLNAMESPACES"
    if w(i + 2) == "AS":
        return True
    if w(i + 2) == "(":
        depth, j = 0, i + 2
        while j < len(toks):
            depth += {"(": 1, ")": -1}.get(w(j), 0)
            if depth == 0:
                return w(j + 1) == "AS"
            j += 1
    return False


def segments(batch: str, offset: int = 0) -> list[Segment]:
    toks = Dialect.get_or_raise("tsql").tokenize(batch)  # pode lançar: quem chama pula o lote
    out: list[Segment] = []
    cur: dict | None = None
    depth = case = 0
    prev = ""

    def close(end_tok: int) -> None:
        nonlocal cur
        if cur is not None:
            s, e = toks[cur["i"]].start, toks[end_tok].end + 1
            out.append(Segment(cur["kind"], offset + s, offset + e, batch[s:e]))
        cur = None

    for i, t in enumerate(toks):
        w = t.text.upper()
        nxt = toks[i + 1].text.upper() if i + 1 < len(toks) else ""
        if w == "(":
            depth += 1
        elif w == ")":
            depth = max(0, depth - 1)
        elif w == "CASE":
            case += 1
        elif w == "END" and case:
            case -= 1
            prev = w
            continue
        if depth == 0 and w == ";":
            if cur is not None:
                close(i - 1)
            prev = w
            continue
        if w == "WITH" and not _is_cte_with(toks, i):
            prev = w
            continue
        if w == "UPDATE" and nxt == "STATISTICS":
            w = "SET"  # não é DML
        if depth == 0 and (w in DML_START or w in STOP) and not (w == "ELSE" and case):
            cont = False
            if cur is not None:
                main = cur.get("main", cur["kind"])
                if w == "SELECT" and prev in ("UNION", "ALL", "EXCEPT", "INTERSECT"):
                    cont = True
                elif cur["kind"] == "WITH" and "main" not in cur and w in DML_START - {"WITH"}:
                    cur["main"] = w
                    cont = True
                elif main == "INSERT" and w in ("SELECT", "EXEC", "EXECUTE") and "src" not in cur:
                    cur["src"] = True
                    cont = True
                elif main == "UPDATE" and w == "SET" and "set" not in cur:
                    cur["set"] = True
                    cont = True
                elif main == "MERGE" and (
                    (w in ("UPDATE", "INSERT", "DELETE") and prev == "THEN")
                    or (w == "SET" and prev == "UPDATE")
                ):
                    cont = True
            if not cont:
                if cur is not None:
                    close(i - 1)
                if w in DML_START:
                    cur = {"i": i, "kind": w}
        prev = w
    if cur is not None:
        close(len(toks) - 1)
    return out


def split_statements(sql: str) -> tuple[list[Segment], list[int]]:
    """Instruções do body inteiro e os offsets dos lotes que o tokenizer rejeitou."""
    segs: list[Segment] = []
    bad: list[int] = []
    for off, batch in split_batches(sql):
        try:
            segs.extend(segments(batch, off))
        except Exception:
            bad.append(off)
    return segs, bad
```
