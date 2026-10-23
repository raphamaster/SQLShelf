from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sqlshelf.core.i18n import set_language
from sqlshelf.ui.tag_field import MAX_SUGGESTIONS, ROW_HEIGHT, TagField

COUNTS = [
    ("relatorio", 4),
    ("dimensão", 3),
    ("bi", 2),
    ("cte", 2),
    ("mensal", 1),
    ("relatorio-mensal", 1),
    ("vendas", 1),
]


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


class _Host(QWidget):
    """A window with the field, another input and an Esc shortcut."""

    def __init__(self) -> None:
        super().__init__()
        self.field = TagField()
        self.other = QLineEdit()
        self.escapes = 0
        layout = QVBoxLayout(self)
        layout.addWidget(self.field)
        layout.addWidget(self.other)
        layout.addStretch(1)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self._on_escape)

    def _on_escape(self) -> None:
        self.escapes += 1


@pytest.fixture
def host() -> Iterator[_Host]:
    _app()
    set_language("en")
    widget = _Host()
    widget.resize(420, 360)
    widget.show()
    widget.activateWindow()
    QTest.qWaitForWindowActive(widget)
    widget.field.set_known_tags(COUNTS)
    widget.field.input().setFocus()
    yield widget
    widget.close()
    set_language("en")


def _type(field: TagField, text: str) -> None:
    QTest.keyClicks(field.input(), text)


def _key(field: TagField, key: Qt.Key) -> None:
    QTest.keyClick(field.input(), key)


# ── Adding and removing ────────────────────────────────────────────────────


def test_enter_adds_the_typed_tag(host: _Host) -> None:
    field = host.field
    spy = QSignalSpy(field.tags_changed)
    _type(field, "Novo")
    _key(field, Qt.Key.Key_Return)
    assert field.tags() == ["novo"]
    assert field.input().text() == ""
    assert not field.popup_visible()
    assert spy.count() == 1


@pytest.mark.parametrize(
    ("typed", "tags", "left"),
    [
        ("bi,", ["bi"], ""),
        ("bi ", ["bi"], ""),
        ("bi, cte", ["bi"], "cte"),
        ("bi, cte ", ["bi", "cte"], ""),
        ("#BI,bi,", ["bi"], ""),
    ],
)
def test_a_comma_or_space_ends_a_tag(
    host: _Host, typed: str, tags: list[str], left: str
) -> None:
    field = host.field
    _type(field, typed)
    assert field.input().text() == left
    assert field.tags() == [*tags, *([left] if left else [])]


def test_the_x_removes_its_tag(host: _Host) -> None:
    field = host.field
    field.set_tags(["bi", "cte"])
    spy = QSignalSpy(field.tags_changed)
    buttons = field.findChildren(QToolButton, "EditTagPillRemove")
    assert [b.toolTip() for b in buttons] == ["Remove bi", "Remove cte"]
    buttons[0].click()
    assert field.tags() == ["cte"]
    assert spy.count() == 1
    assert len(field.findChildren(QToolButton, "EditTagPillRemove")) == 1


def test_backspace_in_the_empty_input_removes_the_last_tag(host: _Host) -> None:
    field = host.field
    field.set_tags(["bi", "cte"])
    _type(field, "x")
    _key(field, Qt.Key.Key_Backspace)
    assert field.tags() == ["bi", "cte"]  # it first erased the "x"
    _key(field, Qt.Key.Key_Backspace)
    assert field.tags() == ["bi"]


def test_set_tags_normalizes_and_drops_duplicates(host: _Host) -> None:
    field = host.field
    field.set_tags(["BI", "#bi", " Cte ", ""])
    assert field.tags() == ["bi", "cte"]
    assert field.input().placeholderText() == ""
    field.set_tags([])
    assert field.input().placeholderText() == "Add a tag…"


def test_tags_include_what_is_still_typed(host: _Host) -> None:
    field = host.field
    field.set_tags(["bi"])
    field.input().setText("Cte bi")
    assert field.tags() == ["bi", "cte"]


# ── Suggestions ────────────────────────────────────────────────────────────


def test_typing_suggests_the_tags_that_contain_it(host: _Host) -> None:
    field = host.field
    _type(field, "mens")
    assert field.popup_visible()
    assert field.suggestions() == [
        ("mensal", 1),
        ("dimensão", 3),
        ("relatorio-mensal", 1),
    ]
    assert field.footer_text() == "creates the tag “mens”"


def test_chosen_tags_are_not_suggested(host: _Host) -> None:
    field = host.field
    field.set_tags(["relatorio"])
    _type(field, "rel")
    assert field.suggestions() == [("relatorio-mensal", 1)]


def test_a_known_tag_typed_in_full_is_added_not_created(host: _Host) -> None:
    field = host.field
    _type(field, "cte")
    assert field.footer_text() == "adds “cte”"


def test_down_then_enter_adds_the_suggestion(host: _Host) -> None:
    field = host.field
    _type(field, "mens")
    _key(field, Qt.Key.Key_Down)
    _key(field, Qt.Key.Key_Down)
    assert field.footer_text() == "adds “dimensão”"
    _key(field, Qt.Key.Key_Up)
    assert field.footer_text() == "adds “mensal”"
    _key(field, Qt.Key.Key_Return)
    assert field.tags() == ["mensal"]
    assert field.input().text() == ""
    assert not field.popup_visible()


def test_down_in_the_empty_input_lists_the_most_used_tags(host: _Host) -> None:
    field = host.field
    field.set_tags(["bi"])
    _key(field, Qt.Key.Key_Down)
    assert field.popup_visible()
    suggestions = field.suggestions()
    assert len(suggestions) == MAX_SUGGESTIONS
    assert suggestions[0] == ("relatorio", 4)
    assert ("bi", 2) not in suggestions
    assert field.footer_text() == ""  # nothing picked, nothing typed


def test_clicking_a_suggestion_adds_it(host: _Host) -> None:
    field = host.field
    _type(field, "mens")
    rows = field._popup.list if field._popup is not None else None
    assert rows is not None
    QTest.mouseClick(
        rows, Qt.MouseButton.LeftButton, pos=QPoint(20, ROW_HEIGHT + ROW_HEIGHT // 2)
    )
    assert field.tags() == ["dimensão"]
    assert not field.popup_visible()
    assert field.input().hasFocus()


def test_the_popup_opens_below_the_field(host: _Host) -> None:
    field = host.field
    _type(field, "mens")
    popup = field._popup
    assert popup is not None and popup.parentWidget() is host
    bottom = field.mapTo(host, QPoint(0, field.height())).y()
    assert popup.y() == bottom + 4
    assert popup.x() == field.mapTo(host, QPoint(0, 0)).x()


def test_no_text_and_no_match_hides_the_popup(host: _Host) -> None:
    field = host.field
    _type(field, "m")
    assert field.popup_visible()
    _key(field, Qt.Key.Key_Backspace)
    assert not field.popup_visible()


# ── Esc and focus ──────────────────────────────────────────────────────────


def test_escape_closes_the_popup_before_the_window_shortcut(host: _Host) -> None:
    field = host.field
    _type(field, "mens")
    _key(field, Qt.Key.Key_Escape)
    assert not field.popup_visible()
    assert host.escapes == 0
    _key(field, Qt.Key.Key_Escape)
    assert host.escapes == 1


def test_focus_marks_the_field_and_leaving_hides_the_popup(host: _Host) -> None:
    field = host.field
    assert field.property("focused") is True
    _type(field, "mens")
    host.other.setFocus()
    assert field.property("focused") is False
    assert not field.popup_visible()


def test_retranslate_updates_the_placeholder_and_the_popup(host: _Host) -> None:
    field = host.field
    _type(field, "mens")
    set_language("pt-BR")
    field.retranslate_ui()
    assert field.footer_text() == "cria a tag “mens”"
    field.set_tags([])
    assert field.input().placeholderText() == "Adicionar tag…"
