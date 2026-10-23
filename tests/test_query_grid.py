from __future__ import annotations

import os
import time as clock
from collections.abc import Callable, Iterator
from datetime import date, datetime, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QImage, QPainter
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from sqlshelf.core.i18n import set_language, tr
from sqlshelf.core.models import SearchResult
from sqlshelf.ui import query_grid as qg
from sqlshelf.ui.paint import GROUP_HEIGHT
from sqlshelf.ui.query_grid import GridSection, QueryGrid

ROOT = Path("C:/lib/Querys")
TODAY = int(datetime.combine(date.today(), time(12, 0)).timestamp())

# Viewport widths that give 1, 2, 3 and 4 columns whatever the scroll bar takes.
ONE_COLUMN = 300
TWO_COLUMNS = 480
THREE_COLUMNS = 700
FOUR_COLUMNS = 900


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _pump(ms: int) -> None:
    # QEventLoop.exec releases the GIL while it waits; QSignalSpy.wait and
    # QTest.qWait do not, which starves the thread that reads the previews.
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = clock.monotonic() + timeout
    while not condition():
        if clock.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        _pump(20)


def _result(
    rel_path: str,
    *,
    folder: Path | None = ROOT,
    title: str = "",
    tags: list[str] | None = None,
    favorite: bool = False,
) -> SearchResult:
    return SearchResult(
        query_id=0,
        rel_path=rel_path,
        title=title or Path(rel_path).stem,
        description="",
        snippet="",
        rank=0.0,
        folder=folder,
        file_mtime=TODAY,
        tags=tags or [],
        tables=[],
        is_favorite=favorite,
    )


def _names(count: int) -> list[SearchResult]:
    return [_result(f"q{n}.sql") for n in range(count)]


def _key(result: SearchResult | None) -> str | None:
    return result.rel_path if result is not None else None


@pytest.fixture
def grid() -> Iterator[QueryGrid]:
    _app()
    set_language("en")
    widget = QueryGrid()
    widget.resize(TWO_COLUMNS, 400)
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()


def _show(grid: QueryGrid, sections: list[GridSection], width: int) -> None:
    grid.resize(width, 400)
    QApplication.processEvents()
    grid.set_sections(sections, set(), None)


def _flat(grid: QueryGrid, count: int, width: int = TWO_COLUMNS) -> list[SearchResult]:
    results = _names(count)
    _show(grid, [GridSection(None, results)], width)
    return results


def _grouped(grid: QueryGrid, width: int = ONE_COLUMN) -> list[SearchResult]:
    results = [_result(n) for n in ("a.sql", "b.sql", "c.sql", "d.sql")]
    sections = [
        GridSection("date:today", results[:3]),
        GridSection("date:older", results[3:]),
    ]
    _show(grid, sections, width)
    return results


def _press(grid: QueryGrid, *keys: Qt.Key) -> None:
    for key in keys:
        QTest.keyClick(grid, key)


def _click(grid: QueryGrid, pos: QPoint) -> None:
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=pos)


def _header_pos(grid: QueryGrid, section: int) -> QPoint:
    row = next(r for r in grid._rows if r.header and r.section == section)
    return QPoint(60, row.top - grid.verticalScrollBar().value() + GROUP_HEIGHT // 2)


# ── Layout ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("width", "columns"),
    [(ONE_COLUMN, 1), (TWO_COLUMNS, 2), (THREE_COLUMNS, 3), (FOUR_COLUMNS, 4)],
)
def test_the_columns_follow_the_width(
    grid: QueryGrid, width: int, columns: int
) -> None:
    _flat(grid, 9, width)

    assert grid._cols == columns
    assert qg.CARD_MIN_WIDTH <= grid._card_width <= qg.CARD_MAX_WIDTH
    used = 2 * qg.MARGIN + columns * grid._card_width + (columns - 1) * qg.GAP
    assert used <= grid.viewport().width()


def test_a_single_wide_column_stops_growing(grid: QueryGrid) -> None:
    _flat(grid, 3, 380)

    assert grid._cols == 1
    assert grid._card_width == qg.CARD_MAX_WIDTH


def test_cards_fill_rows_left_to_right(grid: QueryGrid) -> None:
    _flat(grid, 5, TWO_COLUMNS)

    first, second, third = (grid._card_rect(i) for i in (0, 1, 2))
    assert first.top() == second.top() and second.left() > first.right()
    assert third.top() > first.bottom() and third.left() == first.left()
    assert [grid._card_at(r.center()) for r in (first, second, third)] == [0, 1, 2]


def test_the_gaps_and_margins_are_not_cards(grid: QueryGrid) -> None:
    _flat(grid, 4, TWO_COLUMNS)
    first, second = grid._card_rect(0), grid._card_rect(1)

    between = QPoint((first.right() + second.left()) // 2, first.center().y())
    below = QPoint(first.center().x(), first.bottom() + qg.GAP // 2)
    left_margin = QPoint(qg.MARGIN // 2, first.center().y())

    assert [grid._card_at(p) for p in (between, below, left_margin)] == [-1, -1, -1]


def test_a_short_last_row_has_no_card_after_it(grid: QueryGrid) -> None:
    _flat(grid, 3, TWO_COLUMNS)
    empty_slot = grid._card_rect(2).translated(grid._card_width + qg.GAP, 0)

    assert grid._card_at(empty_slot.center()) == -1


def test_a_long_list_scrolls(grid: QueryGrid) -> None:
    _flat(grid, 40, TWO_COLUMNS)
    bar = grid.verticalScrollBar()

    assert bar.maximum() > 0
    _flat(grid, 2, TWO_COLUMNS)
    assert bar.maximum() == 0


def test_groups_add_a_header_row_each(grid: QueryGrid) -> None:
    _grouped(grid)

    assert [row.header for row in grid._rows] == [
        True,
        False,
        False,
        False,
        True,
        False,
    ]
    assert [grid._header_at(_header_pos(grid, s)) for s in (0, 1)] == [0, 1]


# ── Selection and keyboard ─────────────────────────────────────────────────


def test_the_first_card_is_current_and_nothing_is_announced(grid: QueryGrid) -> None:
    changed = QSignalSpy(grid.current_changed)
    results = _flat(grid, 3)

    assert grid.current_result() is results[0]
    assert changed.count() == 0


def test_the_current_card_is_kept_across_a_refresh(grid: QueryGrid) -> None:
    results = _flat(grid, 4)
    grid.set_sections([GridSection(None, results)], set(), (ROOT, "q2.sql"))

    assert _key(grid.current_result()) == "q2.sql"

    grid.set_sections([GridSection(None, results[:2])], set(), (ROOT, "q2.sql"))
    assert _key(grid.current_result()) == "q0.sql"  # gone: back to the first


def test_no_cards_leave_nothing_current(grid: QueryGrid) -> None:
    _show(grid, [], TWO_COLUMNS)

    assert grid.current_result() is None
    _press(grid, Qt.Key.Key_Down, Qt.Key.Key_Return)  # must not fail


def test_arrow_keys_move_over_the_grid(grid: QueryGrid) -> None:
    _flat(grid, 5, TWO_COLUMNS)  # rows: [0 1] [2 3] [4]
    changed: list[SearchResult] = []
    grid.current_changed.connect(changed.append)

    def current() -> str | None:
        return _key(grid.current_result())

    _press(grid, Qt.Key.Key_Right)
    assert current() == "q1.sql"
    _press(grid, Qt.Key.Key_Down)
    assert current() == "q3.sql"
    _press(grid, Qt.Key.Key_Down)
    assert current() == "q4.sql"  # the last row is short: its last card
    _press(grid, Qt.Key.Key_Down)
    assert current() == "q4.sql"
    _press(grid, Qt.Key.Key_Up)
    assert current() == "q2.sql"
    _press(grid, Qt.Key.Key_Left)
    assert current() == "q1.sql"
    _press(grid, Qt.Key.Key_End)
    assert current() == "q4.sql"
    _press(grid, Qt.Key.Key_Home)
    assert current() == "q0.sql"
    _press(grid, Qt.Key.Key_Left, Qt.Key.Key_Up)  # nowhere to go

    assert [r.rel_path for r in changed] == [
        "q1.sql",
        "q3.sql",
        "q4.sql",
        "q2.sql",
        "q1.sql",
        "q4.sql",
        "q0.sql",
    ]


def test_the_page_keys_jump_a_screenful(grid: QueryGrid) -> None:
    _flat(grid, 40, ONE_COLUMN)
    page = grid.viewport().height() // (qg.CARD_HEIGHT + qg.GAP)

    _press(grid, Qt.Key.Key_PageDown)
    assert _key(grid.current_result()) == f"q{page}.sql"
    _press(grid, Qt.Key.Key_PageUp)
    assert _key(grid.current_result()) == "q0.sql"


def test_up_and_down_step_over_the_group_headers(grid: QueryGrid) -> None:
    _grouped(grid, ONE_COLUMN)  # today: a b c   older: d
    grid.reveal(_result("c.sql"))

    _press(grid, Qt.Key.Key_Down)
    assert _key(grid.current_result()) == "d.sql"
    _press(grid, Qt.Key.Key_Up)
    assert _key(grid.current_result()) == "c.sql"


def test_the_selected_card_is_scrolled_into_view(grid: QueryGrid) -> None:
    _flat(grid, 40, ONE_COLUMN)

    _press(grid, Qt.Key.Key_End)

    card = grid._card_rect(len(grid._cards) - 1)
    assert 0 <= card.top() and card.bottom() < grid.viewport().height()
    bar = grid.verticalScrollBar()
    assert bar.value() >= bar.maximum() - qg.MARGIN  # the end of the list


def test_enter_opens_the_current_card(grid: QueryGrid) -> None:
    _flat(grid, 3)
    opened: list[SearchResult] = []
    grid.activated.connect(opened.append)

    _press(grid, Qt.Key.Key_Right, Qt.Key.Key_Return)

    assert [r.rel_path for r in opened] == ["q1.sql"]


# ── Mouse ──────────────────────────────────────────────────────────────────


def test_a_click_selects_a_card(grid: QueryGrid) -> None:
    _flat(grid, 4)
    changed: list[SearchResult] = []
    grid.current_changed.connect(changed.append)

    _click(grid, grid._card_rect(3).center())
    _click(grid, grid._card_rect(3).center())  # already current: no new signal

    assert [r.rel_path for r in changed] == ["q3.sql"]
    assert _key(grid.current_result()) == "q3.sql"


def test_a_double_click_opens_a_card(grid: QueryGrid) -> None:
    _flat(grid, 4)
    opened: list[SearchResult] = []
    grid.activated.connect(opened.append)

    QTest.mouseDClick(
        grid.viewport(), Qt.MouseButton.LeftButton, pos=grid._card_rect(2).center()
    )

    assert [r.rel_path for r in opened] == ["q2.sql"]


def test_a_click_on_a_star_toggles_the_favorite_without_selecting(
    grid: QueryGrid,
) -> None:
    _flat(grid, 3)
    starred: list[SearchResult] = []
    changed: list[SearchResult] = []
    opened: list[SearchResult] = []
    grid.star_clicked.connect(starred.append)
    grid.current_changed.connect(changed.append)
    grid.activated.connect(opened.append)

    star = qg._star_rect(qg._thumb_rect(grid._card_rect(2))).center()
    _click(grid, star)
    QTest.mouseDClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=star)

    assert starred and {r.rel_path for r in starred} == {"q2.sql"}
    assert changed == [] and opened == []  # a star is not a card
    assert _key(grid.current_result()) == "q0.sql"


def test_a_right_click_asks_for_the_menu_of_the_card_under_it(
    grid: QueryGrid,
) -> None:
    _flat(grid, 3)
    asked: list[tuple[SearchResult, QPoint]] = []
    grid.context_requested.connect(lambda r, p: asked.append((r, p)))
    pos = grid._card_rect(1).center()

    QApplication.sendEvent(
        grid.viewport(),
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, pos, grid.viewport().mapToGlobal(pos)
        ),
    )
    QApplication.sendEvent(
        grid.viewport(),
        QContextMenuEvent(  # on a gap: no menu
            QContextMenuEvent.Reason.Mouse, QPoint(2, 2), QPoint(2, 2)
        ),
    )

    assert [(r.rel_path, p) for r, p in asked] == [
        ("q1.sql", grid.viewport().mapToGlobal(pos))
    ]
    assert _key(grid.current_result()) == "q1.sql"


def test_the_menu_key_opens_the_menu_of_the_current_card(grid: QueryGrid) -> None:
    _flat(grid, 3)
    asked: list[SearchResult] = []
    grid.context_requested.connect(lambda r, _p: asked.append(r))

    QApplication.sendEvent(
        grid,
        QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(), QPoint()),
    )

    assert [r.rel_path for r in asked] == ["q0.sql"]


# ── Groups ─────────────────────────────────────────────────────────────────


def test_a_click_on_a_header_folds_and_unfolds_its_group(grid: QueryGrid) -> None:
    _grouped(grid)
    toggled: list[tuple[str, bool]] = []
    changed: list[SearchResult] = []
    grid.group_toggled.connect(lambda key, folded: toggled.append((key, folded)))
    grid.current_changed.connect(changed.append)
    cards = len(grid._cards)

    _click(grid, _header_pos(grid, 1))
    assert len(grid._cards) == cards - 1
    _click(grid, _header_pos(grid, 1))
    assert len(grid._cards) == cards

    assert toggled == [("date:older", True), ("date:older", False)]
    assert changed == []


def test_a_folded_group_keeps_its_current_card(grid: QueryGrid) -> None:
    _grouped(grid)
    grid.reveal(_result("d.sql"))

    _click(grid, _header_pos(grid, 1))

    assert _key(grid.current_result()) == "d.sql"  # hidden, but still current
    _click(grid, _header_pos(grid, 1))
    assert _key(grid.current_result()) == "d.sql"
    assert grid._current_index == len(grid._cards) - 1


def test_set_sections_folds_the_groups_it_is_given(grid: QueryGrid) -> None:
    results = _grouped(grid)
    sections = [
        GridSection("date:today", results[:3]),
        GridSection("date:older", results[3:]),
    ]

    grid.set_sections(sections, {"date:today"}, (ROOT, "d.sql"))

    assert [r.rel_path for r in grid._cards] == ["d.sql"]
    grid.set_collapsed(set())
    assert len(grid._cards) == 4


def test_reveal_unfolds_the_group_of_the_card(grid: QueryGrid) -> None:
    results = _grouped(grid)
    sections = [
        GridSection("date:today", results[:3]),
        GridSection("date:older", results[3:]),
    ]
    grid.set_sections(sections, {"date:older"}, None)
    toggled: list[tuple[str, bool]] = []
    grid.group_toggled.connect(lambda key, folded: toggled.append((key, folded)))

    grid.reveal(results[3])

    assert _key(grid.current_result()) == "d.sql"
    assert toggled == [("date:older", False)]
    assert results[3] in grid._cards


def test_reveal_ignores_a_card_that_is_not_listed(grid: QueryGrid) -> None:
    _flat(grid, 3)

    grid.reveal(_result("elsewhere.sql"))

    assert _key(grid.current_result()) == "q0.sql"


def test_cards_of_the_same_name_in_two_folders_are_told_apart(grid: QueryGrid) -> None:
    other = Path("C:/lib/Other")
    twins = [_result("a.sql"), _result("a.sql", folder=other)]
    _show(grid, [GridSection(None, twins)], TWO_COLUMNS)

    grid.reveal(twins[1])

    current = grid.current_result()
    assert current is not None and current.folder == other


# ── Tooltips ───────────────────────────────────────────────────────────────


def test_the_tooltip_names_the_card_its_path_and_its_tags(grid: QueryGrid) -> None:
    results = [
        _result("reports/sales.sql", title="Sales", tags=["bi", "monthly"]),
        _result("x.sql"),
    ]
    _show(grid, [GridSection(None, results)], TWO_COLUMNS)

    titled = grid._card_rect(0).center() + QPoint(0, 40)
    untitled = grid._card_rect(1).center() + QPoint(0, 40)

    assert grid._tooltip(titled) == "Sales\nreports/sales.sql\n#bi  #monthly"
    assert grid._tooltip(untitled) == "x.sql"
    assert grid._tooltip(QPoint(2, 2)) == ""


def test_the_tooltip_of_a_star_says_what_it_does(grid: QueryGrid) -> None:
    results = [_result("a.sql"), _result("b.sql", favorite=True)]
    _show(grid, [GridSection(None, results)], TWO_COLUMNS)

    plain = qg._star_rect(qg._thumb_rect(grid._card_rect(0))).center()
    starred = qg._star_rect(qg._thumb_rect(grid._card_rect(1))).center()

    assert grid._tooltip(plain) == tr("context_menu.add_favorite")
    assert grid._tooltip(starred) == tr("context_menu.remove_favorite")


# ── Painting ───────────────────────────────────────────────────────────────


def test_every_card_and_header_paints(grid: QueryGrid, tmp_path: Path) -> None:
    """Paint directly, so an error fails the test instead of being printed."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "sales.sql").write_text(
        "/* ---\ntitle: Sales\n--- */\nSELECT c.Id,\n\tSUM(v.Total) -- total\nFROM t",
        encoding="utf-8",
    )
    results = [
        _result(
            "reports/sales.sql",
            folder=tmp_path,
            title="Variação de vendas por cliente e por mês do ano",
            tags=["bi", "finance", "monthly", "a-very-long-tag-name", "x", "y", "z"],
            favorite=True,
        ),
        _result("missing.sql", folder=tmp_path),
        _result("nowhere.sql", folder=None),
    ]
    sections = [GridSection("date:today", results), GridSection("folder:Q", [])]
    _show(grid, sections, TWO_COLUMNS)
    grid.terms = ["variação", "mês"]
    _wait_until(lambda: not grid._thumbnails._pending)

    image = QImage(grid.viewport().size(), QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        for row in grid._rows:
            if row.header:
                grid._paint_header(painter, row, 0)
        for index in range(len(grid._cards)):
            grid._paint_card(painter, index)
        grid._hover = 0
        grid._hover_star = True
        grid._paint_card(painter, 0)
    finally:
        painter.end()
    grid.viewport().repaint()


def test_a_thumbnail_arrives_after_the_first_paint(
    grid: QueryGrid, tmp_path: Path
) -> None:
    (tmp_path / "a.sql").write_text("SELECT 1 AS One", encoding="utf-8")
    _show(grid, [GridSection(None, [_result("a.sql", folder=tmp_path)])], TWO_COLUMNS)
    repaints: list[bool] = []
    grid._thumbnails.ready.connect(lambda: repaints.append(True))
    path = tmp_path / "a.sql"

    grid.viewport().repaint()  # asks for the text, which is read in the background
    _wait_until(lambda: bool(repaints))

    assert grid._thumbnails.thumbnail(path, TODAY, 1.0) is not None


def test_a_theme_switch_repaints_without_failing(grid: QueryGrid) -> None:
    _flat(grid, 3)

    grid.refresh_theme()

    assert grid._thumbnails._pixmaps == {}
