from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

from sqlshelf.core.i18n import set_language
from sqlshelf.core.navigation import NavState
from sqlshelf.ui.address_bar import AddressBar
from sqlshelf.ui.search_bar import SearchBar


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


@pytest.fixture
def bar() -> Iterator[AddressBar]:
    _app()
    set_language("en")
    widget = AddressBar(SearchBar())
    widget.resize(1100, 52)
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _left(bar: AddressBar, widget: QWidget) -> int:
    return widget.mapTo(bar, QPoint(0, 0)).x()


@pytest.mark.parametrize("width", [1100, 1280, 1920])
def test_the_navigation_comes_first_and_the_search_is_wider(
    bar: AddressBar, width: int
) -> None:
    bar.resize(width, 52)
    QApplication.processEvents()
    navigation = bar._back.parentWidget()
    assert navigation is not None

    assert _left(bar, bar._back) < _left(bar, bar._up) < _left(bar, bar._crumb_frame)
    assert _left(bar, bar._crumb_frame) < _left(bar, bar._search_bar)
    assert navigation.geometry().right() < bar._search_bar.geometry().left()
    assert bar._search_bar.width() > navigation.width()


def test_the_breadcrumb_is_not_clipped_at_the_smallest_window(
    bar: AddressBar,
) -> None:
    bar.set_state(
        NavState(kind="folder", folder=Path("C:/lib/Querys/reports")),
        [Path("C:/lib/Querys")],
        can_back=False,
        can_forward=False,
    )
    QApplication.processEvents()

    crumbs: list[QToolButton] = []
    for i in range(bar._crumbs.count()):
        item = bar._crumbs.itemAt(i)
        widget = item.widget() if item is not None else None
        if isinstance(widget, QToolButton):
            crumbs.append(widget)

    assert [crumb.text() for crumb in crumbs] == ["Library", "Querys", "reports"]
    assert all(crumb.width() >= crumb.sizeHint().width() for crumb in crumbs)
