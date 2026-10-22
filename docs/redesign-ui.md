# Redesign da UI — estilo Explorer / Finder

> **Referência visual:** `docs/redesign/*.png` (5 telas). Este documento cobre o que as imagens não mostram: valores exatos, comportamento, o que não pode regredir e a ordem de implementação.
>
> **Escopo:** camada `sqlshelf/ui/` e tema. Sem mudança no formato dos arquivos `.sql`, no schema do índice ou na semântica da busca. O `CLAUDE.md` continua valendo inteiro (core sem PySide6, nada de I/O na UI thread, arquivos no disco são a fonte da verdade, textos via i18n em EN / PT-BR / ES).

## Telas de referência

| Arquivo | O que mostra |
|---|---|
| `01-navegar-escuro.png` | Janela principal, exibição Detalhes, painel de detalhes em leitura |
| `02-navegar-claro.png` | Tema claro e indexação em andamento na barra de status |
| `03-busca.png` | Busca com chips `table:` / `tag:` e barra de escopo |
| `04-edicao.png` | Painel em modo edição, autocomplete de tags |
| `05-busca-rapida.png` | Busca rápida (Ctrl+P) |

Os textos das imagens estão em PT-BR; no código, as strings de origem continuam em inglês, com tradução nos três locales.

## Decisão: sair do qt-material

O qt-material impõe botões em caixa alta e contornos verdes — exatamente o que o redesenho remove. Substituir por estilo **Fusion + QPalette + QSS gerado a partir de tokens**. Remover `qt-material` do `requirements.txt` e atualizar a tabela *Stack* do `CLAUDE.md`. Nenhuma dependência nova (QtSvg já vem com o PySide6, para os ícones).

## Tokens — `sqlshelf/ui/tokens.py` (Python puro, sem import de Qt)

| Token | Uso | Escuro | Claro |
|---|---|---|---|
| `chrome` | barra de título/endereço, sidebar, status | `#121417` | `#f3f4f6` |
| `bg` | lista, barra de comandos | `#181a1e` | `#ffffff` |
| `panel` | painel de detalhes, popups, diálogos | `#1c1f24` | `#fbfbfc` |
| `field` | campos, segmento ativo | `#23272d` | `#ffffff` |
| `code` | editor SQL e sua faixa de cabeçalho | `#15171a` | `#f7f8fa` |
| `border` | divisórias | `#2a2e34` | `#e3e6ea` |
| `border_strong` | botões de contorno, inputs, popups | `#3a3f46` | `#cdd2d8` |
| `hover` | fundo em hover | `rgba(255,255,255,.05)` | `rgba(0,0,0,.04)` |
| `sel` | linha selecionada, faixa de edição, chips da busca | `rgba(10,222,153,.15)` | `rgba(10,222,153,.18)` |
| `sel_soft` | item ativo da sidebar, pílulas de tag | `rgba(255,255,255,.07)` | `rgba(0,0,0,.06)` |
| `text` | texto principal | `#e9ebee` | `#1a1d21` |
| `text2` | texto secundário | `#b3b9c1` | `#474e57` |
| `text3` | rótulos, contagens, placeholders | `#8b939c` | `#646b73` |
| `accent` | fundo do botão primário, foco, progresso | `#0ADE99` | `#0ADE99` |
| `accent_ink` | texto sobre `accent` | `#052a1d` | `#052a1d` |
| `accent_text` | links e ícones de destaque sobre fundo | `#2fe3a8` | `#04794f` |
| `mark` | destaque de termo buscado | `rgba(10,222,153,.30)` | `rgba(10,222,153,.35)` |
| `overlay` | fundo atrás da busca rápida | `rgba(5,6,8,.55)` | `rgba(20,24,28,.32)` |
| `line_no` | números de linha | `#848c95` | `#656d76` |
| `cur_line` | linha atual do editor | `rgba(255,255,255,.05)` | `rgba(0,0,0,.04)` |
| `star` | favorito | `#f5b524` | `#f5b524` |

**Sintaxe SQL:**

| Token | Escuro | Claro |
|---|---|---|
| keyword (peso 500) | `#6cb6ff` | `#0b5cad` |
| function | `#e7c07b` | `#8a5a00` |
| string | `#f29e74` | `#b1461b` |
| number | `#c9a6ff` | `#6f42c1` |
| comment (itálico) | `#8b939c` | `#6a737d` |
| identifier | `#d7dbe0` | `#24292f` |
| operator | `#a3abb4` | `#57606a` |

- **Alpha em QSS:** `rgba()` em QSS usa alpha de 0 a 255; a conversão fica em `theme.py`.
- **Cor de tag:** paleta fixa `#4c9aff #f5a524 #0ade99 #b38bff #ff7eb6 #ff6b6b #2dd4bf #9aa3ad`, escolhida por `zlib.crc32(nome) % 8`. Não usar `hash()`, que muda a cada processo. Nada é gravado em arquivo.
- **Fontes:**
  - UI: Segoe UI Variable / Segoe UI no Windows; fonte do sistema no Linux.
  - Mono: Cascadia Mono → Consolas → `QFontDatabase.systemFont(FixedFont)`.
  - Tamanhos em px: 13 base · 12 secundário e rótulos · 11 atalhos (kbd) · 16 título do painel · 13 código, com altura de linha 19.
- **Regras visuais:**
  - Nenhum texto em caixa alta.
  - Verde só em seleção, foco e na ação primária.
  - Botões secundários são de contorno (`border_strong`) ou ghost (sem borda, fundo `hover` no hover).
  - Foco: borda `accent` (QSS não tem box-shadow).

## Medidas

**Janela e splitter**
- Janela: mínimo 1100×700.
- Sidebar: 236 px (mínimo 200).
- Lista: flexível (mínimo 360 — regra atual).
- Painel: 520 px (mínimo 420).
- Tamanhos do splitter, painel visível, exibição e modo de agrupamento ficam persistidos em `config.json`.

**Alturas**

| Elemento | px |
|---|---|
| Barra de endereço | 52 |
| Barra de comandos | 46 |
| Barra de status | 28 |
| Linha (Detalhes) | 34 |
| Item da sidebar | 30 |
| Cabeçalho de grupo | 32 |
| Campos | 34 |
| Botões da barra | 30 |
| Botões do painel | 32 |

**Raios:** 4 (chips de tabela, kbd) · 6 (botões, campos, linhas) · 10–12 (pílulas, diálogo).

## Comportamento por área

### Barra de endereço
- **Voltar / Avançar / Acima** (Alt+← / Alt+→ / Alt+↑).
  - O histórico é uma pilha de estados de navegação: pasta, subpasta, tag, Favoritas, Recentes ou busca.
  - Fica numa classe pura `NavHistory` em `sqlshelf/core/navigation.py`.
- **Breadcrumb** clicável por segmento.
  - Botão Reindexar à direita, que dispara a ação existente.
  - Com busca ativa, mostra "Resultados da busca em Biblioteca".
- **Campo de busca:** 320 px; com busca ativa, 520 px. Placeholder "Buscar em {local atual}" e dica `Ctrl F`.

### Barra de comandos
- **Itens, da esquerda para a direita:**
  - Nova consulta — botão primário, Ctrl+N.
  - Abrir pasta — Ctrl+O.
  - Ordenar ▾ — as 4 opções atuais; substitui o botão ⇅.
  - Agrupar ▾ — Data | Pasta | Nenhum.
  - Busca rápida — Ctrl+P.
  - Alternância Detalhes | Conteúdo.
  - Mostrar/ocultar painel — Alt+P.
  - ⋯ (menu).
- **QMenuBar oculta.**
  - O ⋯ abre um QMenu com os submenus atuais (Arquivo, Editar, Exibir, Configurações, Ajuda), reaproveitando as mesmas QActions.
  - Todas as QActions também vão para a janela (`self.addAction(...)`), senão os atalhos param de funcionar com a barra oculta.
  - Item "Mostrar barra de menus" em Exibir, persistido.

### Sidebar
- **Seções:**
  - Todas as consultas / Favoritas / Recentes.
  - Pastas (botão + = Abrir pasta).
  - Tags: as 7 mais usadas com ponto de cor, e "Todas as tags (N)" expande o resto.
- **Subpastas** derivadas dos caminhos já indexados, sem varrer o disco.
  - Filtrar uma pasta é filtrar por prefixo de caminho.
  - A função que monta a árvore é pura e fica em `core`.
- **Contagens** vêm da conexão de leitura, fora da UI thread. Atualizam após indexação e após eventos do watcher.
- **Recentes** mantém a definição atual.
- **Seleção:** fundo `sel_soft`, texto semibold, ícone `accent_text`.

### Lista — exibição Detalhes (padrão)
- **Estrutura:** QTreeView com colunas `[estrela] Nome | Tabelas | Tags | Modificado`.
  - Grupos são itens-pai com `setFirstColumnSpanned`, colapsáveis; grupos vazios ficam ocultos.
  - Clicar no cabeçalho ordena e sincroniza com Ordenar ▾.
- **Agrupar por Data:** Hoje, Ontem, Esta semana, Este mês, Mais antigos.
  - Função pura `date_bucket(mtime, now, first_weekday)` em `sqlshelf/core/grouping.py`.
  - A UI passa `QLocale().firstDayOfWeek()`.
  - Agrupar por Pasta usa rótulos como `Querys › relatorios`.
- **Estrela:** aparece só em hover, na linha selecionada ou quando a consulta é favorita (preenchida em `star`). Precisa de `setMouseTracking(True)`.
- **Nome:**
  - Ícone de arquivo em `accent_text` quando há `title` no frontmatter, em `text3` quando não há.
  - Sem `title`, mostra o nome do arquivo.
  - Tooltip com título completo e nome do arquivo.
- **Tabelas:** lista separada por vírgula, elidida; "—" se vazia.
- **Tags:** as pílulas que couberem, mais "+N".
- **Modificado:** "Hoje 09:42", "Ontem 19:31", senão `dd/mm/aaaa`, com números tabulares. Medir com a fonte da célula (regressão da 1.0.10).
- **Destaque de termos:** fundo `mark` no Nome e em Tabelas para o texto livre e para `table:`.
  - Comparação sem acento e sem diferenciar maiúsculas, com a mesma normalização do FTS.
  - Função pura `match_spans(text, terms) -> list[tuple[int, int]]` em `core`.
  - **Não** reintroduzir `snippet()` do FTS (removido por desempenho na 1.0.10).
- **Estado vazio:** ícone, "Nenhuma consulta aqui" e dica dos operadores.
- **Menu de contexto da linha:** Copiar SQL, Editar metadados, Favoritar, Abrir no SSMS, Copiar caminho, Revelar no Explorer.

### Lista — exibição Conteúdo
Linhas de cerca de 76 px:
- Título semibold e data à direita.
- Descrição, ou "Sem descrição" em `text3`.
- Chip de tabelas (mono, com borda) e todas as tags.

Mesmo modelo, outro `QStyledItemDelegate`.

### Painel de detalhes — leitura
- **Cabeçalho:** ícone 40 px, título (até 2 linhas), nome do arquivo em mono `text3`, botão estrela.
- **Ações:**
  - Copiar SQL — primário; copia sem o frontmatter, como hoje.
  - Editar — Ctrl+E.
  - Abrir no SSMS — no Linux, "Abrir externamente". Usa o mesmo código de abertura do chip de caminho e registra no access log.
  - ⋯ com Selecionar tudo (Ctrl+A), Copiar caminho absoluto, Copiar caminho da pasta e Revelar no Explorer.
- **Removidos:** os botões SELECIONAR TUDO / COPIAR da barra do editor e o botão CTRL+P do cabeçalho.
- **Metadados** em grade rótulo | valor:
  - Descrição em largura total. Sem descrição: "Sem descrição. Use Editar para documentar esta consulta."
  - Tags: clique navega para a tag.
  - Tabelas: clique esquerdo = ir para a ocorrência no editor; botão direito = menu atual com "Buscar em todas as consultas". Comportamento atual mantido.
  - Local: caminho absoluto clicável, com o menu atual.
  - Modificado: data e autor.
- **Faixa sobre o editor:** "SQL · N linhas · encoding · T-SQL". Omitir o encoding se a UI não o tiver à mão.
- **Editor:** o mesmo QPlainTextEdit, com cores de sintaxe vindas dos tokens. Mantém números de linha, linha atual, destaque de ocorrências, a regra de >1000 linhas e a carga assíncrona.

### Painel de detalhes — edição
- **Faixa no topo** com fundo `sel`: "Editando", subtítulo "Os metadados são gravados no cabeçalho do arquivo .sql", Cancelar (Esc) e Salvar (Ctrl+S, primário).
- **Campos:**
  - Título.
  - Descrição (3 linhas).
  - Tags: campo de chips com o autocomplete existente; mostra a contagem por tag, destaca o trecho digitado e tem o rodapé "Enter cria a tag".
  - Autor.
  - Criado (somente leitura).
- **Faixa do editor:** "editável" e "Ln X, Col Y".
- **Gravação** segue o `CLAUDE.md`: preserva campos YAML, atualiza `updated`, cria o bloco se não existir, grava em UTF-8.

### Busca com chips
- **Criação de chips:** `op:valor` seguido de espaço, Enter ou Tab vira chip dentro do campo.
  - × remove o chip; Backspace com o campo vazio remove o último.
  - Operadores: os que `search.py` já aceita (`table`, `col`, `tag`, `date`).
- **Envio:** a string enviada à busca = chips serializados + texto livre. É o mesmo caminho de hoje, sem mudança de semântica.
  - Se `search.py` não expõe tokenização, adicionar `split_tokens()` / `join_tokens()`, com testes de ida e volta (inclusive aspas).
- **Barra de escopo** abaixo dos comandos: "Toda a biblioteca | {pasta atual}", "N resultados" e "Limpar busca".
- Na busca, o agrupamento padrão é Pasta.

### Busca rápida (Ctrl+P)
Reestilização do diálogo atual:
- Sem moldura, 640 px, centralizado.
- Overlay escurecendo a janela; clique fora fecha.
- Seção Consultas: título com destaque e "pasta · data".
- Seção Ações: Nova consulta, Abrir pasta, Reindexar biblioteca — também filtradas pelo texto.
- Rodapé com atalhos.
- Ctrl+C copia o SQL do item selecionado e fecha.

### Barra de status
- **Esquerda:** "N consultas" ou "N resultados" · "1 selecionada".
- **Direita:**
  - Estado do índice: ponto `accent` + "Índice atualizado", ou, durante a indexação, "Indexando {pasta}" + QProgressBar 120×4 + "34 de 61".
  - "Observando N pastas" · "T-SQL".
- Isso **substitui os diálogos modais de progresso** (indexação inicial e Forçar indexação). Atualização via signals do worker.

## Não pode regredir

- [ ] Multi-pastas, Favoritas, Recentes, navegação por tag
- [ ] Busca FTS com `table:` `col:` `tag:` `date:`, combinados; aspas escapadas
- [ ] Ordenação (4 opções) persistente entre buscas; padrão = modificado mais recente
- [ ] Seleção preservada ao reordenar ou atualizar a lista (sem recarregar o arquivo)
- [ ] Editor: highlight, números de linha, linha atual, destaque de ocorrências, regra de >1000 linhas, carga assíncrona
- [ ] Chips de tabela e menu do caminho do arquivo (copiar caminho, copiar pasta, revelar)
- [ ] Copiar SQL sem frontmatter; Selecionar tudo
- [ ] Autocomplete de tags; Nova consulta
- [ ] Access log e diálogo de Relatórios
- [ ] Bandeja do sistema, autostart, verificação de atualizações, Estatísticas, Sobre
- [ ] Troca de tema ao vivo; idiomas EN / PT-BR / ES
- [ ] Watcher atualiza a lista e as contagens
- [ ] Todos os atalhos do README
- [ ] Conexões SQLite separadas para leitura e escrita; nada de I/O na UI thread

## Fatias

Uma por vez. Cada fatia termina com:
- testes `pytest` passando;
- `ruff`, `black` e `mypy` limpos;
- entrada no `CHANGELOG.md` em `[Unreleased]`;
- a caixa marcada aqui.

### F1 — Tema
- [x] **Entrega:** `ui/tokens.py`, `ui/theme.py` (Fusion + QPalette + QSS), remoção do qt-material, `CLAUDE.md` atualizado (stack e ponteiro para este documento).
- **Testes:**
  - Os dois temas têm as mesmas chaves.
  - `tag_color` é estável.
  - Contraste ≥ 4.5:1 de `text`, `text2` e `text3` sobre `bg`, `panel` e `chrome`.
- **Aceite:** troca de tema ao vivo funciona; nenhum texto em caixa alta.

### F2 — Estrutura da janela
- [x] **Entrega:**
  - Splitter de 3 painéis.
  - Barra de endereço, barra de comandos e menu ⋯.
  - Barra de status com progresso.
  - `NavHistory`.
- **Testes:** `NavHistory` (voltar, avançar, acima, descartar o "futuro" ao navegar); atalhos funcionando com a QMenuBar oculta.

### F3 — Sidebar
- [x] **Entrega:** árvore, subpastas, contagens, tags com cor.
- **Testes:** montagem da árvore de subpastas; consultas de contagem no `index_db`.

### F4 — Lista
- [ ] **Entrega:** Detalhes e Conteúdo, grupos, estrela em hover, destaque de termos, estado vazio, menu de contexto.
- **Testes:**
  - `date_bucket`: meia-noite, início da semana, virada de mês.
  - `match_spans`: acentos, maiúsculas, vários termos.
  - Seleção preservada ao reagrupar.

### F5 — Painel de detalhes, leitura
- [ ] Cabeçalho, ações, grade de metadados, faixa do editor.

### F6 — Painel de detalhes, edição
- [ ] Faixa Editando, formulário, Esc / Ctrl+S.

### F7 — Busca com chips e barra de escopo
- [ ] **Testes:** `split_tokens` / `join_tokens` (ida e volta).

### F8 — Busca rápida
- [ ] Reestilização, seção de ações, Ctrl+C.
