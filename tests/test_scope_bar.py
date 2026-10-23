from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from sqlshelf.core.i18n import set_language
from sqlshelf.ui.scope_bar import ScopeBar

FOLDER = Path("D:/Querys")


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


@pytest.fixture
def bar() -> Iterator[ScopeBar]:
    _app()
    set_language("en")
    widget = ScopeBar()
    widget.resize(560, 40)
    widget.show()
    yield widget
    widget.close()
    set_language("en")


def test_the_library_and_the_folder_are_offered(bar: ScopeBar) -> None:
    bar.set_scope(None, FOLDER)
    library, folder = bar.options()
    assert (library.text(), folder.text()) == ("Whole library", "Querys")
    assert library.isChecked() and not folder.isChecked()
    assert folder.toolTip() == str(FOLDER)


def test_without_a_folder_only_the_library_is_offered(bar: ScopeBar) -> None:
    bar.set_scope(None, None)
    assert [b.text() for b in bar.options()] == ["Whole library"]


def test_searching_a_folder_offers_and_checks_it(bar: ScopeBar) -> None:
    bar.set_scope(FOLDER, None)
    library, folder = bar.options()
    assert folder.text() == "Querys" and folder.isChecked()
    assert not library.isChecked()


def test_choosing_an_option_asks_for_it(bar: ScopeBar) -> None:
    bar.set_scope(None, FOLDER)
    spy = QSignalSpy(bar.scope_selected)
    library, folder = bar.options()
    folder.click()
    assert spy.count() == 1 and spy.at(0)[0] == FOLDER
    # The window confirms the move; until then the library stays checked.
    assert library.isChecked() and not folder.isChecked()
    library.click()
    assert spy.count() == 1  # already searched there


def test_the_count_and_clear(bar: ScopeBar) -> None:
    assert bar.count_text() == "" or not bar._count_label.isVisible()
    bar.set_count(3)
    assert bar.count_text() == "3 results"
    bar.set_count(1)
    assert bar.count_text() == "1 result"
    bar.set_count(None)
    assert not bar._count_label.isVisible()
    spy = QSignalSpy(bar.clear_requested)
    bar._clear_btn.click()
    assert spy.count() == 1


def test_retranslate(bar: ScopeBar) -> None:
    bar.set_scope(None, FOLDER)
    bar.set_count(3)
    set_language("pt-BR")
    bar.retranslate_ui()
    assert bar._label.text() == "Buscar em"
    assert bar.options()[0].text() == "Toda a biblioteca"
    assert bar._clear_btn.text() == "Limpar busca"
    assert bar.count_text() == "3 resultados"
