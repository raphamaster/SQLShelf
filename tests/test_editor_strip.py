from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sqlshelf.core.i18n import set_language
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


def test_strip_starts_empty_with_its_title(strip: EditorStrip) -> None:
    assert strip.height() == STRIP_HEIGHT or strip.maximumHeight() == STRIP_HEIGHT
    assert strip._title.text() == "SQL"
    assert strip.info_text() == ""


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
