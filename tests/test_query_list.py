from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QModelIndex, QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from sqlshelf.core.i18n import set_language
from sqlshelf.core.models import SearchResult
from sqlshelf.ui import query_list as ql
from sqlshelf.ui.query_list import QueryListWidget

ROOT = Path("C:/lib/Querys")

# Noon today is "today" whatever the time the tests run, even just after midnight.
TODAY = int(datetime.combine(date.today(), time(12, 0)).timestamp())
OLD = int(datetime(2020, 1, 15, 9, 30).timestamp())


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _result(
    rel_path: str,
    mtime: int = TODAY,
    *,
    title: str = "",
    folder: Path = ROOT,
    tags: list[str] | None = None,
    tables: list[str] | None = None,
) -> SearchResult:
    return SearchResult(
        query_id=0,
        rel_path=rel_path,
        title=title or Path(rel_path).stem,
        description="",
        snippet="",
        rank=0.0,
        folder=folder,
        file_mtime=mtime,
        tags=tags or [],
        tables=tables or [],
    )


@pytest.fixture
def widget() -> Iterator[QueryListWidget]:
    _app()
    set_language("en")
    widget = QueryListWidget()
    widget.resize(900, 500)
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()


def _selections(widget: QueryListWidget) -> list[SearchResult]:
    selected: list[SearchResult] = []
    widget.query_selected.connect(selected.append)
    return selected


def _top_groups(widget: QueryListWidget) -> list[tuple[str, int]]:
    model = widget._model
    return [
        (
            model.index(row, 0).data(ql._ROLE_GROUP),
            model.index(row, 0).data(ql._ROLE_COUNT),
        )
        for row in range(model.rowCount())
    ]


def _current(widget: QueryListWidget) -> str | None:
    result = widget._current_result()
    return result.rel_path if result is not None else None


def _row_centre(widget: QueryListWidget, index: QModelIndex) -> QPoint:
    return widget._view.visualRect(index).center()


def test_date_groups_come_newest_first_and_skip_empty_ones(
    widget: QueryListWidget,
) -> None:
    widget.set_results([_result("old.sql", OLD), _result("a.sql"), _result("b.sql")])

    assert _top_groups(widget) == [("date:today", 2), ("date:older", 1)]
    assert _current(widget) == "a.sql"


def test_oldest_first_reverses_the_date_groups(widget: QueryListWidget) -> None:
    widget.set_sort("mtime_asc")
    widget.set_results([_result("old.sql", OLD), _result("a.sql")])

    assert [key for key, _ in _top_groups(widget)] == ["date:older", "date:today"]


def test_folder_groups_follow_the_sort(widget: QueryListWidget) -> None:
    widget.set_group("folder")
    widget.set_results(
        [
            _result("reports/a.sql", OLD),
            _result("b.sql"),
            _result("reports/c.sql"),
        ]
    )

    assert _top_groups(widget) == [("folder:Querys", 1), ("folder:Querys › reports", 2)]


def test_ungrouped_list_has_no_group_rows(widget: QueryListWidget) -> None:
    widget.set_group("none")
    widget.set_results([_result("a.sql"), _result("b.sql", OLD)])

    assert _top_groups(widget) == [(None, None), (None, None)]


def test_regrouping_keeps_the_selection_without_announcing_it(
    widget: QueryListWidget,
) -> None:
    widget.set_results([_result("a.sql"), _result("reports/b.sql", OLD)])
    widget.select_by_rel_path("reports/b.sql")
    selected = _selections(widget)

    widget.set_group("folder")
    widget.set_group("none")
    widget.set_view("content")
    widget.set_sort("name_desc")

    assert _current(widget) == "reports/b.sql"
    assert selected == []


def test_no_results_show_the_empty_state(widget: QueryListWidget) -> None:
    widget.set_results([])
    assert widget._stack.currentWidget() is widget._empty

    widget.set_results([_result("a.sql")])
    assert widget._stack.currentWidget() is widget._view


def test_a_header_click_sorts_and_asks_to_keep_the_sort(
    widget: QueryListWidget,
) -> None:
    requested: list[str] = []
    widget.sort_requested.connect(requested.append)
    widget.set_results(
        [_result("b.sql", title="Beta"), _result("a.sql", title="Alpha")]
    )
    header = widget._header

    def click(column: int) -> None:
        x = header.sectionViewportPosition(column) + 20
        QTest.mouseClick(
            header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, 10)
        )

    click(ql.COL_NAME)
    assert widget.sort_key() == "name_asc"
    assert [r.rel_path for r in widget._results] == ["a.sql", "b.sql"]
    click(ql.COL_NAME)
    click(ql.COL_MODIFIED)
    click(ql.COL_MODIFIED)
    click(ql.COL_TAGS)  # not sortable

    assert requested == ["name_asc", "name_desc", "mtime_desc", "mtime_asc"]


def test_a_star_click_toggles_the_favorite_without_selecting(
    widget: QueryListWidget,
) -> None:
    widget.set_group("none")
    widget.set_results([_result("a.sql"), _result("b.sql", OLD)])
    toggled: list[SearchResult] = []
    widget.favorite_toggled.connect(toggled.append)
    selected = _selections(widget)

    view = widget._view
    cell = view.visualRect(widget._model.index(1, ql.COL_STAR))
    star = ql._star_rect(cell, False).center()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=star)

    assert [r.rel_path for r in toggled] == ["b.sql"]
    assert _current(widget) == "a.sql"
    assert selected == []


def test_update_favorite_changes_the_listed_result(widget: QueryListWidget) -> None:
    other = _result("a.sql", folder=Path("C:/lib/Other"))
    widget.set_results([_result("a.sql"), other])

    widget.update_favorite("a.sql", True, Path("C:/lib/Other"))

    assert [r.is_favorite for r in widget._results] == [False, True]


def test_keyboard_moves_over_group_rows(widget: QueryListWidget) -> None:
    widget.set_results([_result("a.sql"), _result("old.sql", OLD)])
    selected = _selections(widget)
    view = widget._view

    QTest.keyClick(view, Qt.Key.Key_Down)
    assert _current(widget) == "old.sql"
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert _current(widget) == "old.sql"
    QTest.keyClick(view, Qt.Key.Key_Up)
    assert _current(widget) == "a.sql"
    QTest.keyClick(view, Qt.Key.Key_Up)
    assert _current(widget) == "a.sql"
    QTest.keyClick(view, Qt.Key.Key_End)
    QTest.keyClick(view, Qt.Key.Key_Home)
    assert _current(widget) == "a.sql"

    assert [r.rel_path for r in selected] == ["old.sql", "a.sql", "old.sql", "a.sql"]


def test_a_click_on_a_group_folds_it_and_a_refresh_keeps_it_folded(
    widget: QueryListWidget,
) -> None:
    widget.set_results([_result("a.sql"), _result("old.sql", OLD)])
    view = widget._view
    older = widget._model.index(1, 0)
    assert view.isExpanded(older)

    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=_row_centre(widget, older)
    )
    assert not view.isExpanded(older)
    assert _current(widget) == "a.sql"

    widget.set_results([_result("a.sql"), _result("old.sql", OLD)])
    assert not view.isExpanded(widget._model.index(1, 0))
    assert view.isExpanded(widget._model.index(0, 0))


def test_select_by_rel_path_unfolds_the_group(widget: QueryListWidget) -> None:
    widget.set_results([_result("a.sql"), _result("old.sql", OLD)])
    view = widget._view
    view.setExpanded(widget._model.index(1, 0), False)

    widget.select_by_rel_path("old.sql", ROOT)

    assert view.isExpanded(widget._model.index(1, 0))
    assert _current(widget) == "old.sql"


def test_select_by_rel_path_tells_folders_apart(widget: QueryListWidget) -> None:
    other = Path("C:/lib/Other")
    widget.set_results([_result("a.sql"), _result("a.sql", OLD, folder=other)])

    widget.select_by_rel_path("a.sql", other)

    current = widget._current_result()
    assert current is not None and current.folder == other


def test_content_view_shows_only_the_star_and_name(widget: QueryListWidget) -> None:
    widget.set_results([_result("a.sql")])
    view = widget._view

    widget.set_view("content")
    hidden = [view.isColumnHidden(c) for c in range(ql._COLUMNS)]
    assert hidden == [False, False, True, True, True]
    assert view.isHeaderHidden()
    assert view.itemDelegate() is widget._content_delegate

    widget.set_view("details")
    assert not any(view.isColumnHidden(c) for c in range(ql._COLUMNS))
    assert not view.isHeaderHidden()


@pytest.mark.parametrize(
    ("width", "tables", "tags"),
    [(900, True, True), (560, True, False), (420, False, False)],
)
def test_a_narrow_list_drops_tables_then_tags(
    widget: QueryListWidget, width: int, tables: bool, tags: bool
) -> None:
    widget.set_results([_result("a.sql")])
    widget.resize(width, 400)
    QApplication.processEvents()

    assert widget._view.isColumnHidden(ql.COL_TABLES) is not tables
    assert widget._view.isColumnHidden(ql.COL_TAGS) is not tags
    assert not widget._view.isColumnHidden(ql.COL_MODIFIED)


def test_search_terms_are_marked(widget: QueryListWidget) -> None:
    widget.set_results([_result("a.sql")], highlight="table:Pedidos tag:bi venda")
    assert widget._view.terms == ["venda", "Pedidos"]

    widget.set_results([_result("a.sql")])
    assert widget._view.terms == []


def test_name_tooltip_shows_the_path_under_a_title(widget: QueryListWidget) -> None:
    widget.set_group("none")
    widget.set_results(
        [_result("reports/sales.sql", title="Sales by client"), _result("x.sql", OLD)]
    )
    view = widget._view
    name = view.visualRect(widget._model.index(0, ql.COL_NAME)).center()
    untitled = view.visualRect(widget._model.index(1, ql.COL_NAME)).center()

    assert view._tooltip(name) == "Sales by client\nreports/sales.sql"
    assert view._tooltip(untitled) == "x.sql"


def test_header_labels_follow_the_language(widget: QueryListWidget) -> None:
    model = widget._model
    assert model.headerData(ql.COL_NAME, Qt.Orientation.Horizontal) == "Name"
    try:
        set_language("pt-BR")
        widget.retranslate_ui()
        assert model.headerData(ql.COL_NAME, Qt.Orientation.Horizontal) == "Nome"
    finally:
        set_language("en")


@pytest.mark.parametrize("view_mode", ["details", "content"])
def test_every_cell_paints(widget: QueryListWidget, view_mode: str) -> None:
    """Paint each cell directly, so a delegate error fails the test instead of
    being printed by Qt."""
    widget.set_view(view_mode)
    widget.set_results(
        [
            _result(
                "reports/sales.sql",
                title="Variação de vendas",
                tags=["bi", "finance", "monthly", "a-very-long-tag-name"],
                tables=["dbo.Pedidos", "dbo.Clientes", "dbo.ItensPedido"],
            ),
            _result("untitled.sql", OLD),
        ],
        highlight="table:Pedidos variação",
    )
    view = widget._view
    view.selectionModel().clearSelection()
    delegate = (
        widget._content_delegate if view_mode == "content" else widget._details_delegate
    )
    assert view.itemDelegate() is delegate
    image = QImage(900, 120, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        model = widget._model
        for group_row in range(model.rowCount()):
            group = model.index(group_row, 0)
            indexes = [group] + [
                model.index(row, column, group)
                for row in range(model.rowCount(group))
                for column in range(ql._COLUMNS)
            ]
            for index in indexes:
                option = QStyleOptionViewItem()
                delegate.initStyleOption(option, index)
                option.rect = QRect(
                    0, 0, 300, delegate.sizeHint(option, index).height()
                )
                delegate.paint(painter, option, index)
        for column in range(ql._COLUMNS):
            widget._header.paintSection(painter, QRect(0, 0, 150, 32), column)
    finally:
        painter.end()
