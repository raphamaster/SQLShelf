from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sqlshelf.core.dialects import resolve
from sqlshelf.core.i18n import set_language
from sqlshelf.core.query_graph import Span, StatementInfo
from sqlshelf.ui.diagram_view import DiagramCounts
from sqlshelf.ui.editor_strip import (
    STRIP_HEIGHT,
    EditorStrip,
    dialect_tooltip,
    strip_info,
)

TSQL = resolve("tsql", "")


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


@pytest.fixture
def strip() -> Iterator[EditorStrip]:
    _app()
    set_language("en")
    widget = EditorStrip()
    yield widget
    set_language("en")


@pytest.mark.parametrize(
    ("lines", "encoding", "expected"),
    [
        (12, "UTF-8", "12 lines · UTF-8 · T-SQL"),
        (1, "UTF-8", "1 line · UTF-8 · T-SQL"),
        (0, "UTF-16 LE", "0 lines · UTF-16 LE · T-SQL"),
        (3, "", "3 lines · T-SQL"),
    ],
)
def test_strip_info(lines: int, encoding: str, expected: str) -> None:
    set_language("en")
    assert strip_info(lines, encoding, "T-SQL") == expected


@pytest.mark.parametrize(
    ("declared", "body", "expected"),
    [
        ("PL/SQL", "SELECT 1", "Oracle: declared in the file header"),
        (
            None,
            "SELECT name FROM dual WHERE ROWNUM < 5",
            "Oracle: read from the syntax (DUAL, ROWNUM)",
        ),
        (None, "SELECT 1", "No dialect detected: read as generic SQL"),
    ],
)
def test_dialect_tooltip(declared: str | None, body: str, expected: str) -> None:
    set_language("en")
    assert dialect_tooltip(resolve(declared, body)) == expected


def test_strip_starts_empty_on_the_sql_tab(strip: EditorStrip) -> None:
    assert strip.height() == STRIP_HEIGHT or strip.maximumHeight() == STRIP_HEIGHT
    assert strip.tab() == "sql"
    assert strip._sql_tab.text() == "SQL"
    assert strip._diagram_tab.text() == "Diagram"
    assert strip._sql_tab.isChecked()
    assert strip.info_text() == ""
    assert strip.counts_text() == ""
    assert strip.zoom_text() == "100%"


def test_set_info_then_clear(strip: EditorStrip) -> None:
    strip.set_info(6, "UTF-8", resolve(None, "SELECT id::text FROM t"))
    assert strip.info_text() == "6 lines · UTF-8 · PostgreSQL"
    assert strip._info.toolTip() == "PostgreSQL: read from the syntax (::)"
    strip.clear()
    assert strip.info_text() == ""
    assert strip._info.toolTip() == ""


def test_retranslate_keeps_the_loaded_facts(strip: EditorStrip) -> None:
    strip.set_info(6, "Windows-1252", TSQL)
    set_language("pt-BR")
    strip.retranslate_ui()
    assert strip.info_text() == "6 linhas · Windows-1252 · T-SQL"
    assert strip._info.toolTip() == "T-SQL: declarado no cabeçalho do arquivo"


def test_retranslate_leaves_an_empty_strip_empty(strip: EditorStrip) -> None:
    set_language("pt-BR")
    strip.retranslate_ui()
    assert strip.info_text() == ""


def test_editing_shows_editable_and_the_cursor(strip: EditorStrip) -> None:
    strip.set_info(6, "UTF-8", TSQL)
    assert strip.mode_text() == ""
    assert strip.cursor_text() == ""

    strip.set_editing(True)
    strip.set_cursor(10, 27)
    assert strip.is_editing()
    assert strip.mode_text() == "editable"
    assert strip.cursor_text() == "Ln 10, Col 27"

    strip.set_lines(7)
    assert strip.info_text() == "7 lines · UTF-8 · T-SQL"

    strip.set_editing(False)
    assert not strip.is_editing()
    assert strip.cursor_text() == ""


def test_retranslate_keeps_the_cursor(strip: EditorStrip) -> None:
    strip.set_editing(True)
    strip.set_cursor(2, 5)
    set_language("es")
    strip.retranslate_ui()
    assert strip.cursor_text() == "Lín 2, Col 5"
    assert strip.mode_text() == "editable"


def test_clear_leaves_edit_mode(strip: EditorStrip) -> None:
    strip.set_editing(True)
    strip.clear()
    assert not strip.is_editing()


def test_set_tab_switches_quietly(strip: EditorStrip) -> None:
    picked: list[str] = []
    strip.tab_changed.connect(picked.append)
    strip.set_tab("diagram")
    assert strip.tab() == "diagram"
    assert strip._diagram_tab.isChecked()
    assert not strip._sql_tab.isChecked()
    strip.set_tab("nonsense")
    assert strip.tab() == "diagram"
    assert picked == []


def test_clicking_a_tab_emits_once(strip: EditorStrip) -> None:
    picked: list[str] = []
    strip.tab_changed.connect(picked.append)
    strip._diagram_tab.click()
    strip._diagram_tab.click()
    strip._sql_tab.click()
    assert picked == ["diagram", "sql"]
    assert strip._sql_tab.isChecked()


def test_toggle_tab_flips_and_emits(strip: EditorStrip) -> None:
    picked: list[str] = []
    strip.tab_changed.connect(picked.append)
    strip.toggle_tab()
    strip.toggle_tab()
    assert picked == ["diagram", "sql"]


def test_each_tab_shows_its_own_facts(strip: EditorStrip) -> None:
    strip.set_info(6, "UTF-8", TSQL)
    strip.set_editing(True)
    strip.set_cursor(1, 1)
    assert not strip._info.isHidden()
    assert not strip._cursor.isHidden()
    assert strip._counts_label.isHidden()
    assert strip._controls.isHidden()

    strip.set_tab("diagram")
    assert strip._info.isHidden()
    assert strip._mode.isHidden()
    assert strip._cursor.isHidden()
    assert not strip._counts_label.isHidden()
    assert not strip._controls.isHidden()


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (DiagramCounts(4, 3, 2, True), "4 tables · 3 joins · 2 filters"),
        (DiagramCounts(1, 0, 1, True), "1 table · 1 filter"),
        (DiagramCounts(3, 1, 0, False), "3 objects · 1 join"),
        (DiagramCounts(0, 0, 0, True), ""),
        (None, ""),
    ],
)
def test_counts(
    strip: EditorStrip, counts: DiagramCounts | None, expected: str
) -> None:
    strip.set_counts(counts)
    assert strip.counts_text() == expected


def test_counts_follow_the_language(strip: EditorStrip) -> None:
    strip.set_counts(DiagramCounts(4, 3, 2, True))
    set_language("pt-BR")
    strip.retranslate_ui()
    assert strip.counts_text() == "4 tabelas · 3 junções · 2 filtros"
    assert strip._diagram_tab.text() == "Diagrama"


def test_clear_drops_the_counts(strip: EditorStrip) -> None:
    strip.set_counts(DiagramCounts(1, 0, 0, True))
    strip.clear()
    assert strip.counts_text() == ""


def test_zoom_controls_emit(strip: EditorStrip) -> None:
    fired: list[str] = []
    strip.fit_requested.connect(lambda: fired.append("fit"))
    strip.zoom_in_requested.connect(lambda: fired.append("in"))
    strip.zoom_out_requested.connect(lambda: fired.append("out"))
    strip.zoom_reset_requested.connect(lambda: fired.append("reset"))
    strip._fit.click()
    strip._zoom_in.click()
    strip._zoom_out.click()
    strip._zoom.click()
    assert fired == ["fit", "in", "out", "reset"]
    strip.set_zoom(75)
    assert strip.zoom_text() == "75%"


STATEMENTS = [
    StatementInfo(1, "SELECT", "SELECT … INTO #a & b", 1, Span(0, 10), "ok"),
    StatementInfo(2, "UPDATE", "UPDATE dbo.T", 3, Span(12, 30), "ok"),
    StatementInfo(3, "SELECT", "SELECT FROM WHERE ((", 5, Span(32, 50), "skipped"),
]


def test_statement_selector(strip: EditorStrip) -> None:
    strip.set_statements(STATEMENTS, 0)
    assert strip._statement_button.isHidden()  # on the SQL tab
    strip.set_tab("diagram")
    assert not strip._statement_button.isHidden()
    actions = [a for a in strip.statement_menu().actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == [
        "All (3)",
        "1 · SELECT … INTO #a && b",
        "2 · UPDATE dbo.T",
        "3 · SELECT FROM WHERE ((",
    ]
    assert [a.isEnabled() for a in actions] == [True, True, True, False]
    assert [a.isChecked() for a in actions] == [True, False, False, False]
    assert strip.statement_text() == "All (3)"

    picked: list[int] = []
    strip.statement_changed.connect(picked.append)
    actions[2].trigger()
    assert picked == [2]
    assert strip.current_statement() == 2
    assert strip.statement_text() == "2 · UPDATE dbo.T"
    assert [a.isChecked() for a in actions] == [False, False, True, False]
    actions[2].trigger()  # the same one again
    assert picked == [2]
    assert actions[2].isChecked()

    strip.set_statements(STATEMENTS[:1], 0)
    assert strip._statement_button.isHidden()  # one statement: nothing to pick


def test_statement_selector_follows_the_language(strip: EditorStrip) -> None:
    strip.set_statements(STATEMENTS[:2], 0)
    set_language("pt-BR")
    strip.retranslate_ui()
    assert strip.statement_text() == "Todas (2)"
    assert strip.statement_menu().actions()[0].text() == "Todas (2)"


def test_stale_shows_on_the_diagram_tab(strip: EditorStrip) -> None:
    strip.set_statements(STATEMENTS, 2)
    strip.set_stale(True)
    assert strip.is_stale()
    assert strip._stale_label.isHidden()
    strip.set_tab("diagram")
    assert not strip._stale_label.isHidden()
    assert strip._stale_label.text() == "outdated"
    strip.clear()
    assert not strip.is_stale()
    assert strip._stale_label.isHidden()
    assert strip.current_statement() == 0
    assert strip._statement_button.isHidden()


def test_more_menu(strip: EditorStrip) -> None:
    rearranged: list[bool] = []
    toggled: list[bool] = []
    strip.rearrange_requested.connect(lambda: rearranged.append(True))
    strip.results_toggled.connect(toggled.append)
    texts = [a.text() for a in strip.more_menu().actions() if not a.isSeparator()]
    assert texts == [
        "Rearrange",
        "Show result",
        "Export PNG…",
        "Export SVG…",
        "Copy image",
    ]
    actions = strip.more_actions()
    assert set(actions) == {"rearrange", "results", "png", "svg", "copy"}
    actions["rearrange"].trigger()
    actions["results"].trigger()
    assert rearranged == [True]
    assert toggled == [False]
    strip.set_show_results(True)
    assert actions["results"].isChecked()
    assert toggled == [False]  # quietly


def test_exports_wait_for_a_diagram(strip: EditorStrip) -> None:
    exported: list[str] = []
    strip.export_requested.connect(exported.append)
    actions = strip.more_actions()
    assert not any(actions[kind].isEnabled() for kind in ("png", "svg", "copy"))

    strip.set_counts(DiagramCounts(1, 0, 0, True))
    assert all(actions[kind].isEnabled() for kind in ("png", "svg", "copy"))
    actions["svg"].trigger()
    actions["copy"].trigger()
    assert exported == ["svg", "copy"]

    strip.set_counts(None)
    assert not actions["png"].isEnabled()


def test_exports_follow_the_language(strip: EditorStrip) -> None:
    set_language("pt-BR")
    strip.retranslate_ui()
    actions = strip.more_actions()
    assert actions["png"].text() == "Exportar PNG…"
    assert actions["copy"].text() == "Copiar imagem"


def test_expand_asks_for_the_window(strip: EditorStrip) -> None:
    fired: list[bool] = []
    strip.expand_requested.connect(lambda: fired.append(True))
    assert strip._expand.toolTip() == "Open in a window"
    strip.set_tab("diagram")
    assert not strip._expand.isHidden()
    strip._expand.click()
    assert fired == [True]
