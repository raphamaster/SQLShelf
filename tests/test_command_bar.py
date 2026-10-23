from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMenu

from sqlshelf.core import config as cfg
from sqlshelf.core.i18n import set_language
from sqlshelf.ui.command_bar import CommandBar


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


@pytest.fixture
def bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CommandBar]:
    monkeypatch.setattr(cfg, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_CONFIG_FILE", tmp_path / "config.json")
    _app()
    set_language("en")
    menu = QMenu()
    widget = CommandBar(menu)
    widget.resize(1100, 46)
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    menu.deleteLater()
    set_language("en")


def test_the_menu_comes_first_and_the_view_options_last(bar: CommandBar) -> None:
    order = [
        bar._more,
        bar._new,
        bar._open,
        bar._sort_btn,
        bar._group_btn,
        bar._quick,
        bar._details,
        bar._content,
        bar._grid,
        bar._panel,
    ]

    lefts = [button.x() for button in order]

    assert lefts == sorted(lefts)
    assert len(set(lefts)) == len(lefts)


def test_the_folder_button_says_import(bar: CommandBar) -> None:
    assert bar._open._text.text() == "Import folder"

    set_language("pt-BR")
    bar.retranslate_ui()

    assert bar._open._text.text() == "Importar pasta"


@pytest.mark.parametrize("mode", ["details", "content", "grid"])
def test_a_view_button_asks_for_its_view(bar: CommandBar, mode: str) -> None:
    asked: list[str] = []
    bar.view_requested.connect(asked.append)

    bar._view_buttons[mode].click()

    assert asked == [mode]


@pytest.mark.parametrize("mode", ["details", "content", "grid"])
def test_set_view_checks_only_that_view(bar: CommandBar, mode: str) -> None:
    bar.set_view(mode)

    checked = [name for name, b in bar._view_buttons.items() if b.isChecked()]
    assert checked == [mode]


def test_an_unknown_view_falls_back_to_details(bar: CommandBar) -> None:
    bar.set_view("grid")
    bar.set_view("bogus")

    assert bar._details.isChecked() and not bar._grid.isChecked()


def test_the_grid_button_has_a_tooltip_in_every_language(bar: CommandBar) -> None:
    assert bar._grid.toolTip() == "Grid view"

    set_language("pt-BR")
    bar.retranslate_ui()
    assert bar._grid.toolTip() == "Ver em grade"

    set_language("es")
    bar.retranslate_ui()
    assert bar._grid.toolTip() == "Ver en cuadrícula"
