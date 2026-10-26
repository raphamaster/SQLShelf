from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sqlshelf.core.i18n import set_language
from sqlshelf.ui.diagram_view import DiagramCounts
from sqlshelf.ui.editor_strip import STRIP_HEIGHT, EditorStrip, strip_info


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
    assert strip_info(lines, encoding) == expected


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
    strip.set_info(6, "UTF-8")
    assert strip.info_text() == "6 lines · UTF-8 · T-SQL"
    strip.clear()
    assert strip.info_text() == ""


def test_retranslate_keeps_the_loaded_facts(strip: EditorStrip) -> None:
    strip.set_info(6, "Windows-1252")
    set_language("pt-BR")
    strip.retranslate_ui()
    assert strip.info_text() == "6 linhas · Windows-1252 · T-SQL"


def test_retranslate_leaves_an_empty_strip_empty(strip: EditorStrip) -> None:
    set_language("pt-BR")
    strip.retranslate_ui()
    assert strip.info_text() == ""


def test_editing_shows_editable_and_the_cursor(strip: EditorStrip) -> None:
    strip.set_info(6, "UTF-8")
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
    strip.set_info(6, "UTF-8")
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
