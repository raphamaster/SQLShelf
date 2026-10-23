from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from sqlshelf.core.i18n import set_language
from sqlshelf.core.models import SearchResult
from sqlshelf.ui.command_palette import (
    LIST_MAX_HEIGHT,
    WIDTH,
    CommandPalette,
)


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _result(rel_path: str, title: str, tables: list[str], mtime: int) -> SearchResult:
    return SearchResult(
        query_id=0,
        rel_path=rel_path,
        title=title,
        description="",
        snippet="",
        rank=0.0,
        tags=["bi"],
        folder=Path("D:/Querys"),
        tables=tables,
        file_mtime=mtime,
    )


SALES = _result("reports/sales.sql", "Monthly sales", ["Orders"], 300)
BALANCE = _result("finance/balance.sql", "Balance", ["Titles"], 200)
LOGINS = _result("audit/logins.sql", "Logins by user", ["Logins"], 100)
RESULTS = [LOGINS, SALES, BALANCE]


def _flush_deletions() -> None:
    """Run the pending deleteLater() calls, so no overlay outlives its test."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


class _Host(QWidget):
    """A window with an input that has the focus before the palette opens."""

    def __init__(self) -> None:
        super().__init__()
        self.other = QLineEdit()
        layout = QVBoxLayout(self)
        layout.addWidget(self.other)
        layout.addStretch(1)


@pytest.fixture
def host() -> Iterator[_Host]:
    _app()
    set_language("en")
    widget = _Host()
    widget.resize(1000, 700)
    widget.show()
    widget.activateWindow()
    QTest.qWaitForWindowActive(widget)
    widget.other.setFocus()
    yield widget
    widget.close()
    _flush_deletions()
    set_language("en")


@pytest.fixture
def palette(host: _Host) -> CommandPalette:
    widget = CommandPalette(host)
    widget.open()
    widget.set_results(RESULTS)
    return widget


def _input(palette: CommandPalette) -> QLineEdit:
    return palette._input


def _type(palette: CommandPalette, text: str) -> None:
    # ASCII only: QTest.keyClicks crashes the process on accented letters.
    QTest.keyClicks(_input(palette), text)


def _key(
    palette: CommandPalette,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    QTest.keyClick(_input(palette), key, modifier)


def _kinds(palette: CommandPalette) -> list[str]:
    return [kind for kind, _value in palette.rows()]


# ── Rows ────────────────────────────────────────────────────────────────────


def test_opening_lists_the_queries_then_the_actions(palette: CommandPalette) -> None:
    assert palette.is_open()
    assert palette.rows() == [
        ("section", "Queries"),
        ("query", SALES),
        ("query", BALANCE),
        ("query", LOGINS),
        ("section", "Actions"),
        ("action", "new_query"),
        ("action", "open_folder"),
        ("action", "reindex"),
    ]
    assert palette.current() == ("query", SALES)
    assert QApplication.focusWidget() is _input(palette)


def test_before_the_queries_arrive_only_the_actions_show(host: _Host) -> None:
    palette = CommandPalette(host)
    palette.open()
    assert _kinds(palette) == ["section", "action", "action", "action"]
    assert palette.current() == ("action", "new_query")
    palette.set_results([SALES])
    assert palette.current() == ("query", SALES)


def test_typing_filters_queries_and_actions(palette: CommandPalette) -> None:
    _type(palette, "sales")
    assert palette.rows() == [("section", "Queries"), ("query", SALES)]
    _input(palette).clear()
    _type(palette, "folder")
    assert palette.rows() == [("section", "Actions"), ("action", "open_folder")]
    assert palette.current() == ("action", "open_folder")


def test_nothing_found_says_so(palette: CommandPalette) -> None:
    _type(palette, "zzz")
    assert palette.rows() == [("empty", "No matches")]
    assert palette.current() is None


def test_operators_narrow_the_queries_and_hide_the_actions(
    palette: CommandPalette,
) -> None:
    _type(palette, "table:Titles")
    assert palette.rows() == [("section", "Queries"), ("query", BALANCE)]


# ── Keyboard ────────────────────────────────────────────────────────────────


def test_arrows_skip_the_section_titles(palette: CommandPalette) -> None:
    _key(palette, Qt.Key.Key_Up)
    assert palette.current() == ("query", SALES)  # nothing above
    for _ in range(3):
        _key(palette, Qt.Key.Key_Down)
    assert palette.current() == ("action", "new_query")
    _key(palette, Qt.Key.Key_Up)
    assert palette.current() == ("query", LOGINS)


def test_enter_opens_the_selected_query(host: _Host, palette: CommandPalette) -> None:
    opened = QSignalSpy(palette.query_selected)
    closed = QSignalSpy(palette.closed)
    _key(palette, Qt.Key.Key_Down)
    _key(palette, Qt.Key.Key_Return)
    assert opened.count() == 1 and opened.at(0)[0] is BALANCE
    assert closed.count() == 1
    assert not palette.is_open()


def test_enter_runs_the_selected_action(palette: CommandPalette) -> None:
    spy = QSignalSpy(palette.action_requested)
    _type(palette, "reindex")
    _key(palette, Qt.Key.Key_Enter)
    assert spy.count() == 1 and spy.at(0)[0] == "reindex"
    assert not palette.is_open()


def test_ctrl_c_copies_the_selected_query(palette: CommandPalette) -> None:
    spy = QSignalSpy(palette.copy_requested)
    _key(palette, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert spy.count() == 1 and spy.at(0)[0] is SALES
    assert not palette.is_open()


def test_ctrl_c_copies_the_typed_text_when_it_is_selected(
    palette: CommandPalette,
) -> None:
    spy = QSignalSpy(palette.copy_requested)
    _type(palette, "sales")
    _input(palette).selectAll()
    _key(palette, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert spy.count() == 0
    assert palette.is_open()


def test_ctrl_c_on_an_action_does_nothing(palette: CommandPalette) -> None:
    spy = QSignalSpy(palette.copy_requested)
    _type(palette, "folder")
    _key(palette, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert spy.count() == 0
    assert palette.is_open()


def test_tab_turns_the_last_word_into_a_filter(palette: CommandPalette) -> None:
    _type(palette, "Orders")
    assert _kinds(palette) == ["empty"]  # not in a name, path or tag
    _key(palette, Qt.Key.Key_Tab)
    assert palette.text() == "table:Orders"
    assert palette.rows() == [("section", "Queries"), ("query", SALES)]
    _key(palette, Qt.Key.Key_Tab)
    assert palette.text() == "tag:Orders"
    _key(palette, Qt.Key.Key_Tab)
    assert palette.text() == "Orders"
    assert QApplication.focusWidget() is _input(palette)


def test_escape_closes_and_gives_the_focus_back(
    host: _Host, palette: CommandPalette
) -> None:
    spy = QSignalSpy(palette.closed)
    _key(palette, Qt.Key.Key_Escape)
    assert spy.count() == 1
    assert not palette.is_open()
    assert QApplication.focusWidget() is host.other


def test_closing_twice_is_harmless(palette: CommandPalette) -> None:
    spy = QSignalSpy(palette.closed)
    palette.close_palette()
    palette.close_palette()
    assert spy.count() == 1


def test_closing_the_window_closes_the_palette_and_frees_both() -> None:
    _app()
    window = _Host()
    window.show()
    palette = CommandPalette(window)
    palette.open()
    window.close()
    assert not palette.is_open()
    # The palette holds no reference to its window: dropping the window frees
    # it now, and the palette's pending deletion has nothing left to delete.
    freed = weakref.ref(window)
    del window
    assert freed() is None
    _flush_deletions()


# ── Mouse and focus ─────────────────────────────────────────────────────────


def test_a_click_outside_the_card_closes(palette: CommandPalette) -> None:
    card = palette.card_geometry()
    QTest.mouseClick(palette, Qt.MouseButton.LeftButton, pos=card.center())
    assert palette.is_open()
    below = QPoint(card.center().x(), card.bottom() + 20)
    QTest.mouseClick(palette, Qt.MouseButton.LeftButton, pos=below)
    assert not palette.is_open()


def test_a_click_on_a_row_opens_it(palette: CommandPalette) -> None:
    spy = QSignalSpy(palette.query_selected)
    view = palette._list
    rect = view.visualItemRect(view.item(3))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert spy.count() == 1 and spy.at(0)[0] is LOGINS


def test_moving_the_focus_away_closes(host: _Host, palette: CommandPalette) -> None:
    host.other.setFocus()
    assert not palette.is_open()
    assert QApplication.focusWidget() is host.other


# ── Geometry and language ───────────────────────────────────────────────────


def test_the_card_is_centred_and_fits_the_window(
    host: _Host, palette: CommandPalette
) -> None:
    card = palette.card_geometry()
    assert card.width() == WIDTH
    assert card.left() == (host.width() - WIDTH) // 2
    assert palette._list.height() <= LIST_MAX_HEIGHT
    host.resize(500, 300)
    QApplication.processEvents()
    card = palette.card_geometry()
    assert palette.geometry() == host.rect()
    assert card.width() == 500 - 32
    assert card.bottom() < host.height()


def test_retranslate(palette: CommandPalette) -> None:
    set_language("pt-BR")
    palette.retranslate_ui()
    assert _input(palette).placeholderText() == "Buscar consultas e ações…"
    rows = palette.rows()
    assert rows[0] == ("section", "Consultas")
    assert ("section", "Ações") in rows
