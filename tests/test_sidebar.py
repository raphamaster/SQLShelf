from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from sqlshelf.core.folder_tree import build_folder_tree
from sqlshelf.core.i18n import set_language
from sqlshelf.core.library_summary import LibrarySummary
from sqlshelf.core.navigation import NavState
from sqlshelf.ui import sidebar as sb
from sqlshelf.ui.sidebar import SidebarWidget

ROOT = Path("C:/lib/Querys")
OTHER = Path("C:/lib/Other")


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _summary(
    tags: list[tuple[str, int]] | None = None, *, with_trees: bool = True
) -> LibrarySummary:
    trees = (
        {
            ROOT: build_folder_tree(
                ROOT, ["a.sql", "reports/b.sql", "reports/monthly/c.sql"]
            ),
            OTHER: build_folder_tree(OTHER, ["d.sql"]),
        }
        if with_trees
        else None
    )
    return LibrarySummary(
        total=4,
        favorites=1,
        recent=2,
        tags=tags if tags is not None else [("bi", 3), ("finance", 1)],
        trees=trees,
    )


@pytest.fixture
def sidebar() -> Iterator[SidebarWidget]:
    _app()
    set_language("en")
    widget = SidebarWidget()
    widget.resize(236, 700)
    widget.set_folders([(ROOT, True), (OTHER, False)])
    widget.set_summary(_summary())
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    yield widget
    widget.close()


def _item(widget: SidebarWidget, kind: str, value: object) -> QTreeWidgetItem:
    return widget._items[(kind, value)]


def _rows(widget: SidebarWidget) -> list[tuple[str, str, object]]:
    """(kind, text, count) of every visible row, top to bottom."""
    rows = []
    item = widget._tree.topLevelItem(0)
    while item is not None:
        if not item.isHidden():
            rows.append((item.data(0, sb._KIND), item.text(0), item.data(0, sb._COUNT)))
        item = widget._tree.itemBelow(item)
    return rows


def _click(widget: SidebarWidget, item: QTreeWidgetItem, pos: QPoint | None = None):
    rect = widget._tree.visualItemRect(item)
    point = (
        pos if pos is not None else QPoint(rect.center().x() + 40, rect.center().y())
    )
    QTest.mouseClick(
        widget._tree.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier(0),
        point,
    )


def test_rows_show_views_folders_and_tags_with_counts(sidebar: SidebarWidget) -> None:
    assert _rows(sidebar) == [
        ("view", "All queries", 4),
        ("view", "Favorites", 1),
        ("view", "Recent", 2),
        ("header", "Folders", None),
        ("folder", "Querys", 3),
        ("folder", "Other", 1),
        ("header", "Tags", None),
        ("tag", "bi", 3),
        ("tag", "finance", 1),
    ]


def test_counts_are_blank_until_the_first_summary() -> None:
    _app()
    set_language("en")
    widget = SidebarWidget()
    widget.set_folders([(ROOT, False)])
    assert [row[2] for row in _rows(widget)] == [None, None, None, None, None]


def test_an_empty_library_shows_the_hint() -> None:
    _app()
    set_language("en")
    widget = SidebarWidget()
    kinds = [row[0] for row in _rows(widget)]
    assert kinds == ["view", "view", "view", "header", "hint"]


def test_subfolders_nest_under_their_library_folder(sidebar: SidebarWidget) -> None:
    root = _item(sidebar, "folder", ROOT)
    reports = _item(sidebar, "folder", ROOT / "reports")
    monthly = _item(sidebar, "folder", ROOT / "reports" / "monthly")
    assert reports.parent() is root and monthly.parent() is reports
    assert (reports.data(0, sb._DEPTH), monthly.data(0, sb._DEPTH)) == (1, 2)
    assert reports.data(0, sb._COUNT) == 2
    assert not root.isExpanded()


def test_clicking_a_folder_expands_it_and_selects_it(sidebar: SidebarWidget) -> None:
    spy = QSignalSpy(sidebar.folder_selected)
    root = _item(sidebar, "folder", ROOT)
    _click(sidebar, root)
    assert spy.count() == 1 and spy.at(0)[0] == ROOT
    assert root.isExpanded()


def test_clicking_the_chevron_only_toggles(sidebar: SidebarWidget) -> None:
    spy = QSignalSpy(sidebar.folder_selected)
    root = _item(sidebar, "folder", ROOT)
    rect = sidebar._tree.visualItemRect(root)
    chevron = QPoint(sb._CHEVRON_X + 7, rect.center().y())
    _click(sidebar, root, chevron)
    assert root.isExpanded() and spy.count() == 0
    _click(sidebar, root, chevron)
    assert not root.isExpanded() and spy.count() == 0


def test_subfolder_click_selects_its_path(sidebar: SidebarWidget) -> None:
    spy = QSignalSpy(sidebar.folder_selected)
    _item(sidebar, "folder", ROOT).setExpanded(True)
    _click(sidebar, _item(sidebar, "folder", ROOT / "reports"))
    assert spy.at(spy.count() - 1)[0] == ROOT / "reports"


def test_plus_on_the_folders_header_asks_for_a_folder(sidebar: SidebarWidget) -> None:
    spy = QSignalSpy(sidebar.open_folder_requested)
    header = sidebar._tree.topLevelItem(3)
    assert header is not None and header.data(0, sb._VALUE) == "folders"
    rect = sidebar._tree.visualItemRect(header)
    _click(sidebar, header, QPoint(rect.center().x(), rect.center().y()))
    assert spy.count() == 0  # the header itself does nothing
    _click(sidebar, header, sb._plus_rect(rect).center())
    assert spy.count() == 1


@pytest.mark.parametrize(
    ("kind", "value", "signal", "args"),
    [
        ("view", "all", "tag_selected", [""]),
        ("view", "favorites", "favorites_selected", []),
        ("view", "recent", "recent_selected", []),
        ("tag", "finance", "tag_selected", ["finance"]),
    ],
)
def test_views_and_tags_emit_their_signal(
    sidebar: SidebarWidget, kind: str, value: str, signal: str, args: list
) -> None:
    spy = QSignalSpy(getattr(sidebar, signal))
    _click(sidebar, _item(sidebar, kind, value))
    assert spy.count() == 1
    assert list(spy.at(0)) == args


@pytest.mark.parametrize(
    ("state", "key"),
    [
        (NavState(), ("view", "all")),
        (NavState("favorites"), ("view", "favorites")),
        (NavState("recent", ROOT), ("view", "recent")),
        (NavState("tag", ROOT, "bi"), ("tag", "bi")),
        (NavState("folder", ROOT), ("folder", ROOT)),
        (NavState("search", ROOT / "reports", "x"), ("folder", ROOT / "reports")),
        (NavState("search", None, "x"), ("view", "all")),
    ],
)
def test_exactly_one_row_is_active(
    sidebar: SidebarWidget, state: NavState, key: tuple[str, object]
) -> None:
    sidebar.show_state(state)
    active = [k for k, item in sidebar._items.items() if item.data(0, sb._ACTIVE)]
    assert active == [key]


def test_an_active_subfolder_opens_its_ancestors(sidebar: SidebarWidget) -> None:
    sidebar.show_state(NavState("folder", ROOT / "reports" / "monthly"))
    assert _item(sidebar, "folder", ROOT).isExpanded()
    assert _item(sidebar, "folder", ROOT / "reports").isExpanded()


def test_long_tag_lists_fold_behind_all_tags(sidebar: SidebarWidget) -> None:
    tags = [(f"t{i}", 10 - i) for i in range(10)]
    sidebar.set_summary(_summary(tags))
    visible = [row for row in _rows(sidebar) if row[0] in ("tag", "more")]
    assert [row[1] for row in visible] == [f"t{i}" for i in range(7)] + [
        "All tags (10)"
    ]

    assert sidebar._more_item is not None
    _click(sidebar, sidebar._more_item)
    visible = [row for row in _rows(sidebar) if row[0] in ("tag", "more")]
    assert len(visible) == 11 and visible[-1][1] == "Fewer tags"


def test_a_hidden_active_tag_unfolds_the_list(sidebar: SidebarWidget) -> None:
    sidebar.set_summary(_summary([(f"t{i}", 10 - i) for i in range(10)]))
    sidebar.show_state(NavState("tag", None, "t9"))
    assert not _item(sidebar, "tag", "t9").isHidden()


def test_a_summary_without_trees_keeps_the_subfolders(
    sidebar: SidebarWidget,
) -> None:
    sidebar.set_summary(_summary([("bi", 1)], with_trees=False))
    assert (("folder", ROOT / "reports")) in sidebar._items
    assert _item(sidebar, "tag", "bi").data(0, sb._COUNT) == 1


def test_expanded_folders_survive_a_rebuild(sidebar: SidebarWidget) -> None:
    _item(sidebar, "folder", ROOT).setExpanded(True)
    sidebar.set_summary(_summary([("bi", 5)]))
    assert _item(sidebar, "folder", ROOT).isExpanded()


def test_favorite_library_folders_are_flagged(sidebar: SidebarWidget) -> None:
    assert _item(sidebar, "folder", ROOT).data(0, sb._FAVORITE)
    assert not _item(sidebar, "folder", OTHER).data(0, sb._FAVORITE)
