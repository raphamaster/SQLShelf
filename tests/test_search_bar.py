from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from sqlshelf.core.i18n import set_language
from sqlshelf.ui.search_bar import ACTIVE_WIDTH, CHIP_VALUE_MAX, WIDTH, SearchBar


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


class _Host(QWidget):
    """A window with the search bar and another input to move focus to."""

    def __init__(self) -> None:
        super().__init__()
        self.bar = SearchBar()
        self.other = QLineEdit()
        layout = QVBoxLayout(self)
        layout.addWidget(self.bar)
        layout.addWidget(self.other)


@pytest.fixture
def host() -> Iterator[_Host]:
    _app()
    set_language("en")
    widget = _Host()
    widget.resize(600, 120)
    widget.show()
    widget.activateWindow()
    QTest.qWaitForWindowActive(widget)
    widget.bar.focus()
    yield widget
    widget.close()
    set_language("en")


def _type(bar: SearchBar, text: str) -> None:
    # ASCII only: QTest.keyClicks crashes the process on accented letters.
    QTest.keyClicks(bar._edit, text)


def _key(bar: SearchBar, key: Qt.Key) -> None:
    QTest.keyClick(bar._edit, key)


def _last(spy: QSignalSpy) -> str:
    return str(spy.at(spy.count() - 1)[0])


# ── Tokens become chips ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "tokens", "free"),
    [
        ("table:Pedidos ", [("table", "Pedidos")], ""),
        ("table:Pedidos", [], "table:Pedidos"),  # still being typed
        (
            "table:Pedidos tag:relatorio variacao",
            [("table", "Pedidos"), ("tag", "relatorio")],
            "variacao",
        ),
        ("vendas col:Id ", [("col", "Id")], "vendas "),
        ("Table:x ", [], "Table:x "),
        ("date:15/06/2026 ", [("date", "15/06/2026")], ""),
    ],
)
def test_a_space_turns_a_token_into_a_chip(
    host: _Host, typed: str, tokens: list[tuple[str, str]], free: str
) -> None:
    bar = host.bar
    _type(bar, typed)
    assert bar.tokens() == tokens
    assert bar.free_text() == free
    assert [chip.token() for chip in bar.chips()] == [f"{o}:{v}" for o, v in tokens]


def test_the_search_is_the_chips_then_the_text(host: _Host) -> None:
    bar = host.bar
    spy = QSignalSpy(bar.search_changed)
    _type(bar, "variacao table:Pedidos ")
    assert bar.text() == "table:Pedidos variacao "
    assert _last(spy) == "table:Pedidos variacao "


def test_enter_turns_every_token_into_a_chip(host: _Host) -> None:
    bar = host.bar
    _type(bar, "abc tag:bi")
    _key(bar, Qt.Key.Key_Return)
    assert bar.tokens() == [("tag", "bi")]
    assert bar.free_text() == "abc"


def test_tab_ends_a_token_and_keeps_the_focus(host: _Host) -> None:
    bar = host.bar
    _type(bar, "col:Id")
    _key(bar, Qt.Key.Key_Tab)
    assert bar.tokens() == [("col", "Id")]
    assert bar._edit.hasFocus()
    _type(bar, "x")
    _key(bar, Qt.Key.Key_Tab)  # nothing to end: Tab moves on as usual
    assert host.other.hasFocus()
    assert bar.free_text() == "x"


def test_a_token_typed_before_the_text_keeps_the_cursor(host: _Host) -> None:
    bar = host.bar
    _type(bar, "abc")
    bar._edit.setCursorPosition(0)
    _type(bar, "tag:x ")
    assert bar.tokens() == [("tag", "x")]
    assert bar.free_text() == "abc"
    assert bar._edit.cursorPosition() == 0


# ── Removing chips ─────────────────────────────────────────────────────────


def test_the_x_removes_its_chip(host: _Host) -> None:
    bar = host.bar
    bar.set_text_silently("tag:a tag:b free")
    spy = QSignalSpy(bar.search_changed)
    chips = bar.chips()
    assert chips[1].remove_btn.toolTip() == "Remove tag:b"
    chips[1].remove_btn.click()
    assert bar.tokens() == [("tag", "a")]
    assert spy.count() == 1
    assert _last(spy) == "tag:a free"
    assert bar._edit.hasFocus()


def test_backspace_in_the_empty_input_removes_the_last_chip(host: _Host) -> None:
    bar = host.bar
    _type(bar, "tag:a tag:b x")
    _key(bar, Qt.Key.Key_Backspace)
    assert bar.tokens() == [("tag", "a"), ("tag", "b")]  # it erased the "x"
    _key(bar, Qt.Key.Key_Backspace)
    assert bar.tokens() == [("tag", "a")]
    assert bar.text() == "tag:a"


def test_clear_removes_everything_once(host: _Host) -> None:
    bar = host.bar
    bar.set_text_silently("tag:a free")
    assert bar._clear_btn.isVisible() and not bar._kbd.isVisible()
    spy = QSignalSpy(bar.search_changed)
    bar._clear_btn.click()
    assert bar.text() == ""
    assert bar.chips() == []
    assert spy.count() == 1 and _last(spy) == ""
    assert bar._kbd.isVisible() and not bar._clear_btn.isVisible()
    bar.clear()
    assert spy.count() == 1  # already empty


# ── Showing a search ───────────────────────────────────────────────────────


def test_set_text_silently_shows_tokens_as_chips(host: _Host) -> None:
    bar = host.bar
    spy = QSignalSpy(bar.search_changed)
    text = "table:Pedidos tag:relatorio variação"
    bar.set_text_silently(text)
    assert bar.tokens() == [("table", "Pedidos"), ("tag", "relatorio")]
    assert bar.free_text() == "variação"
    assert bar.text() == text
    assert spy.count() == 0
    bar.set_text_silently("")
    assert bar.chips() == [] and bar.text() == ""
    assert spy.count() == 0


def test_the_placeholder_yields_to_chips(host: _Host) -> None:
    bar = host.bar
    bar.set_location("Library")
    assert bar._edit.placeholderText() == "Search in Library"
    bar.set_text_silently("tag:bi")
    assert bar._edit.placeholderText() == ""


def test_a_long_value_is_shortened(host: _Host) -> None:
    bar = host.bar
    bar.set_active(True)
    value = "VeryLongTableNameThatDoesNotFit" * 3
    bar.set_text_silently(f"table:{value}")
    QApplication.processEvents()
    label = bar.chips()[0].value_label
    assert label.width() <= CHIP_VALUE_MAX
    assert label.text().endswith("…")
    assert label.toolTip() == value
    assert label.full_text() == value


def test_active_widens_the_field(host: _Host) -> None:
    bar = host.bar
    assert bar.width() == WIDTH
    bar.set_active(True)
    assert bar.width() == ACTIVE_WIDTH


def test_focus_marks_the_field(host: _Host) -> None:
    bar = host.bar
    assert bar._field.property("focused") is True
    host.other.setFocus()
    assert bar._field.property("focused") is False


def test_retranslate_updates_the_chips(host: _Host) -> None:
    bar = host.bar
    bar.set_text_silently("tag:bi")
    set_language("pt-BR")
    bar.retranslate_ui()
    assert bar.chips()[0].remove_btn.toolTip() == "Remover tag:bi"
    assert bar._clear_btn.toolTip() == "Limpar busca"
