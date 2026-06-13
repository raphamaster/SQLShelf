1. Montagem da janela principal
main_window.py, método _build_ui() (linhas 199–300).
Layout: QSplitter horizontal com três filhos adicionados na ordem sidebar → lista → painel de detalhe. Tamanhos iniciais fixos: [180, 320, 900]. Definido como setCentralWidget.

2. qt-material
Chamado em main.py:18:


qt_material.apply_stylesheet(app, theme="dark_teal.xml")
Por cima, há QSS custom inline espalhado em vários arquivos (não há um .qss externo): toolbar e editor-header em main_window.py:240-276, painel de metadados em metadata_panel.py:40-45, chips em tag_widget.py:89-101, gutter do editor em code_editor.py:28-31.

3. Lista de queries
query_list.py usa QListWidget puro — sem QListView + model e sem QStyledItemDelegate. Cada item é um QListWidgetItem(r.title) simples; as tags ficam só no tooltip (#tag1  #tag2). Nada é renderizado visualmente além do título.

4. Botões Edit / Save / Cancel
Definidos em main_window.py:245-261 como QPushButton dentro de um QToolBar. A flag self._edit_mode: bool (linha 104) controla o estado. Dois métodos:

_enter_edit_mode() — desabilita setReadOnly(False) no editor, chama metadata_panel.set_edit_mode(True), habilita Save/Cancel.
_cancel_edit_mode() — inverte tudo e relê o arquivo do disco para descartar mudanças.
5. Campo de busca → FTS5
search_bar.py:17: textChanged → search_changed signal.
main_window.py:218: conectado a _on_search_changed() → _run_search().
_run_search() chama db.search(text), que em search.py parseia prefixos table:/col:/tag: e monta um MATCH FTS5 com bm25() ranking. Busca é reativa por caractere, sem debounce.

6. Constantes de cor
Não existe arquivo centralizado. As cores estão inline em:

Arquivo	Cores hardcoded
code_editor.py	_GUTTER_BG, _NUM_INACTIVE, _NUM_CURRENT, _LINE_HIGHLIGHT (únicos definidos como constantes de classe)
tag_widget.py	_CHIP_BG, _CHIP_FG (constantes de módulo)
metadata_panel.py	~8 hex colors inline
main_window.py	#1c1c2e, #3a3a60 inline
sidebar.py	#aaaaaa, #777777 inline
highlighter.py	4 cores de syntax highlight inline
Resumo para o redesign: a lista de queries é o ponto com mais potencial de ganho imediato (trocar QListWidget por QListView + delegate renderizando título + tags + caminho). As cores dispersas pedem um theme.py central antes de qualquer mudança visual. O QSplitter já é flexível o suficiente para reposicionamento de painéis.