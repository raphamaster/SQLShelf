from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sqlshelf.core import config as cfg
from sqlshelf.core.dialects import get
from sqlshelf.core.frontmatter import read_sql_file
from sqlshelf.core.i18n import set_language
from sqlshelf.core.navigation import NavState
from sqlshelf.core.query_graph import QueryGraph, build_query_graph
from sqlshelf.ui import main_window as mw
from sqlshelf.ui.command_palette import CommandPalette
from sqlshelf.ui.diagram_view import GraphCache
from sqlshelf.ui.diagram_window import DiagramWindow
from sqlshelf.ui.main_window import MainWindow
from sqlshelf.ui.theme import apply_theme


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _pump(ms: int) -> None:
    # QEventLoop.exec releases the GIL while it waits; QTest.qWait does not,
    # which starves the index and search workers.
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        _pump(20)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    (root / "reports").mkdir(parents=True)
    (root / "sales.sql").write_text(
        "/* ---\ntitle: Sales\ntags: [bi]\n--- */\nSELECT * FROM sales",
        encoding="utf-8",
    )
    (root / "reports" / "clients.sql").write_text(
        "SELECT id FROM clients", encoding="utf-8"
    )
    return root


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cfg, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfg, "_CONFIG_FILE", config_dir / "config.json")
    set_language("en")
    app = _app()
    apply_theme(app, "light")

    win = MainWindow()
    win._startup_update_timer.stop()
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win)
    yield win
    win._quitting = True
    win.close()
    if win._diagram_window is not None:
        # Delete it now: a menu left for deleteLater holds the last reference
        # to it through its actions' slots.
        win._diagram_window.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        win._diagram_window = None


def _load(win: MainWindow, folder: Path) -> None:
    win.load_folder(folder)
    _wait_until(lambda: win._index_jobs == 0 and win._query_list.count() == 2)


def test_menu_bar_is_hidden_but_its_shortcuts_are_on_the_window(
    window: MainWindow,
) -> None:
    assert window.menuBar().isHidden()
    shortcuts = {action.shortcut().toString() for action in window.actions()}
    for key in ("Ctrl+O", "Ctrl+N", "Ctrl+Shift+C", "F1", "Alt+Left", "Alt+P"):
        assert key in shortcuts


def test_alt_p_toggles_the_details_panel_with_the_menu_bar_hidden(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    assert not window._panel.isHidden()

    QTest.keyClick(window, Qt.Key.Key_P, Qt.KeyboardModifier.AltModifier)
    assert window._panel.isHidden()
    assert cfg.get_panel_visible() is False

    QTest.keyClick(window, Qt.Key.Key_P, Qt.KeyboardModifier.AltModifier)
    assert not window._panel.isHidden()
    assert window._splitter.sizes()[2] > 0


def test_alt_arrows_walk_the_navigation_history(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    window._navigate(NavState("favorites"))
    assert window._nav.current == NavState("favorites")

    QTest.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert window._nav.current == NavState("folder", library)

    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert window._nav.current == NavState("favorites")


def test_typing_a_search_is_one_history_entry(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    for text in ("sa", "sal", "sales"):
        window._search_bar._edit.setText(text)
        window._on_search_timeout()
    _wait_until(lambda: window._query_list.count() == 1)
    assert window._nav.current == NavState("search", library, "sales")

    window.go_back()
    assert window._nav.current == NavState("folder", library)
    assert window._search_bar.text() == ""
    _wait_until(lambda: window._query_list.count() == 2)

    window.go_forward()
    assert window._search_bar.text() == "sales"


def test_up_leaves_a_tag_for_its_folder_then_the_library(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    window._on_tag_selected("bi")
    assert window._search_bar.text() == "tag:bi"
    _wait_until(lambda: window._query_list.count() == 1)

    window.go_up()
    assert window._nav.current == NavState("folder", library)
    window.go_up()
    assert window._nav.current == NavState("all")
    assert not window._up_act.isEnabled()


def test_status_bar_reports_the_library_state(
    window: MainWindow, library: Path
) -> None:
    status = window._status_bar
    assert status._counts.text().startswith("No folder open")

    _load(window, library)
    assert status._counts.text().startswith("2 queries")
    assert status._index.text() == "Index up to date"
    assert status._watch.text() == "Watching 1 folder"
    assert status._dialect.isHidden()  # no query open


def test_flash_replaces_the_counts_then_restores_them(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    status = window._status_bar
    status.flash("Saved: sales.sql", timeout=50)
    assert status._counts.text() == "Saved: sales.sql"
    _wait_until(lambda: status._counts.text().startswith("2 queries"))


def _sidebar_count(win: MainWindow, kind: str, value: object) -> object:
    item = win._sidebar._items.get((kind, value))
    return None if item is None else item.data(0, Qt.ItemDataRole.UserRole + 2)


def test_sidebar_shows_the_folder_tree_with_counts(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _wait_until(lambda: _sidebar_count(window, "folder", library / "reports") == 1)
    assert _sidebar_count(window, "view", "all") == 2
    assert _sidebar_count(window, "folder", library) == 2
    assert _sidebar_count(window, "tag", "bi") == 1


def test_a_subfolder_narrows_the_list_and_the_tags(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _wait_until(lambda: _sidebar_count(window, "folder", library / "reports") == 1)

    window._sidebar.folder_selected.emit(library / "reports")
    assert window._nav.current == NavState("folder", library / "reports")
    _wait_until(lambda: window._query_list.count() == 1)
    assert len(window._known_dbs) == 1  # a subfolder is not a new library
    # Tags follow the folder in view; "bi" only tags a file outside it.
    _wait_until(lambda: _sidebar_count(window, "tag", "bi") is None)
    assert _sidebar_count(window, "view", "all") == 2


def test_opening_a_query_updates_the_recent_count(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _wait_until(lambda: _sidebar_count(window, "view", "recent") == 0)
    window._query_list.select_by_rel_path("sales.sql")
    _wait_until(lambda: _sidebar_count(window, "view", "recent") == 1)


def _select(win: MainWindow, rel_path: str) -> None:
    win._query_list.select_by_rel_path(rel_path)
    _wait_until(lambda: win._query_loaded)


def test_selecting_a_query_fills_the_strip_and_enables_the_actions(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    panel = window._metadata_panel
    _select(window, "sales.sql")
    # Nothing in "SELECT * FROM sales" names a dialect.
    assert window._editor_strip.info_text() == "1 line · UTF-8 · SQL"
    assert panel._title_label.full_text() == "Sales"
    assert panel._file_name_label.full_text() == "sales.sql"
    assert panel._copy_btn.isEnabled()
    assert panel._edit_btn.isEnabled()


@pytest.mark.parametrize(
    ("header", "body", "label", "tooltip"),
    [
        (
            "dialect: PL/SQL\n",
            "SELECT * FROM sales",
            "Oracle",
            "Oracle: declared in the file header",
        ),
        ("", "SELECT id::text FROM sales", "PostgreSQL", "PostgreSQL: read from "),
        ("", "SELECT * FROM sales", "SQL", "No dialect detected"),
    ],
)
def test_the_strip_status_bar_and_colours_follow_the_dialect(
    window: MainWindow,
    library: Path,
    header: str,
    body: str,
    label: str,
    tooltip: str,
) -> None:
    (library / "sales.sql").write_text(
        f"/* ---\ntitle: Sales\n{header}--- */\n{body}", encoding="utf-8"
    )
    _load(window, library)
    _select(window, "sales.sql")
    strip = window._editor_strip
    status = window._status_bar
    assert strip.info_text().endswith(f" · {label}")
    assert strip._info.toolTip().startswith(tooltip)
    assert not status._dialect.isHidden()
    assert status._dialect.text() == label
    assert status._dialect.toolTip() == strip._info.toolTip()
    assert get(window._highlighter.dialect()).label == label

    window._reset_editor()  # as when a search finds nothing
    assert status._dialect.isHidden()


def test_copy_sql_copies_the_body_without_frontmatter(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _select(window, "sales.sql")
    window._metadata_panel._copy_btn.click()
    assert QApplication.clipboard().text() == "SELECT * FROM sales"


def test_ctrl_e_edits_only_a_loaded_query(window: MainWindow, library: Path) -> None:
    _load(window, library)
    window._on_edit_shortcut()
    assert not window._edit_mode

    _select(window, "sales.sql")
    window._on_edit_shortcut()
    assert window._edit_mode
    assert not window._editor.isReadOnly()
    window._on_edit_shortcut()
    assert not window._edit_mode
    assert window._editor.isReadOnly()


def test_escape_outside_edit_mode_does_not_reload(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _select(window, "sales.sql")
    window._cancel_edit_mode()
    assert window._query_loaded
    assert window._metadata_panel._copy_btn.isEnabled()


def test_a_tag_pill_shows_the_queries_with_the_tag(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _select(window, "sales.sql")
    window._metadata_panel._tags_area.chips()[0].click()
    assert window._search_bar.text() == "tag:bi"
    _wait_until(lambda: window._query_list.count() == 1)


def _edit(win: MainWindow, rel_path: str = "sales.sql") -> None:
    _select(win, rel_path)
    win._on_edit_shortcut()
    assert win._edit_mode


def test_saving_writes_the_edits_and_keeps_the_other_fields(
    window: MainWindow, library: Path
) -> None:
    sales = library / "sales.sql"
    sales.write_text(
        "/* ---\ntitle: Sales\ncreated: 2026-01-01\ncustom: keep\ntags: [bi]\n"
        "--- */\nSELECT * FROM sales",
        encoding="utf-8",
    )
    _load(window, library)
    _edit(window)
    panel = window._metadata_panel
    assert panel._created_edit.text() == "01/01/2026"
    panel._title_edit.setText("Monthly sales")
    panel._desc_edit.setPlainText("Sales per month")
    panel._tag_field.set_tags(["bi", "vendas"])
    panel._author_edit.setText("ana")

    QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    _wait_until(lambda: not window._saving and window._query_loaded)

    assert not window._edit_mode
    assert window._editor.isReadOnly()
    assert not window._editor_strip.is_editing()
    meta, body, _ = read_sql_file(sales)
    assert meta["title"] == "Monthly sales"
    assert meta["description"] == "Sales per month"
    assert meta["tags"] == ["bi", "vendas"]
    assert meta["author"] == "ana"
    assert meta["custom"] == "keep"
    assert str(meta["created"]) == "2026-01-01"
    assert "updated" in meta
    assert "dialect" not in meta  # automatic writes nothing
    assert body == "SELECT * FROM sales"
    assert panel._title_label.full_text() == "Monthly sales"


def test_the_form_offers_the_detected_dialect_and_saves_a_choice(
    window: MainWindow, library: Path
) -> None:
    sales = library / "sales.sql"
    sales.write_text("SELECT SYSDATE FROM dual WHERE ROWNUM <= 5", encoding="utf-8")
    _load(window, library)
    _edit(window)
    combo = window._metadata_panel._dialect_combo
    assert combo.currentText() == "Automatic (detected: Oracle)"

    combo.setCurrentIndex(combo.findData("postgres"))
    window.save_current()
    _wait_until(lambda: not window._saving and window._query_loaded)
    assert read_sql_file(sales)[0]["dialect"] == "postgres"

    _edit(window)
    assert combo.currentText() == "PostgreSQL"
    combo.setCurrentIndex(0)
    window.save_current()
    _wait_until(lambda: not window._saving and window._query_loaded)
    assert "dialect" not in read_sql_file(sales)[0]


def test_a_failed_save_stays_in_edit_mode(
    window: MainWindow, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[object, ...]] = []
    monkeypatch.setattr(mw.QMessageBox, "critical", lambda *args: errors.append(args))
    _load(window, library)
    _edit(window)
    sales = library / "sales.sql"
    sales.chmod(stat.S_IREAD)
    try:
        window._metadata_panel._title_edit.setText("Lost?")
        window.save_current()
        _wait_until(lambda: not window._saving)
    finally:
        sales.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert len(errors) == 1
    assert window._edit_mode
    assert not window._editor.isReadOnly()
    assert window._metadata_panel._title_edit.text() == "Lost?"
    assert read_sql_file(sales)[0]["title"] == "Sales"


def test_escape_closes_the_tag_popup_then_cancels_the_edit(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _edit(window)
    field = window._metadata_panel._tag_field
    field.input().setFocus()
    QTest.keyClicks(field.input(), "zz")
    assert field.popup_visible()

    QTest.keyClick(field.input(), Qt.Key.Key_Escape)
    assert not field.popup_visible()
    assert window._edit_mode

    QTest.keyClick(field.input(), Qt.Key.Key_Escape)
    assert not window._edit_mode
    _wait_until(lambda: window._query_loaded)
    assert read_sql_file(library / "sales.sql")[0]["tags"] == ["bi"]


def test_the_strip_shows_the_cursor_while_editing(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _edit(window)
    strip = window._editor_strip
    assert strip.is_editing()
    assert strip.mode_text() == "editable"
    cursor = window._editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    window._editor.setTextCursor(cursor)
    assert strip.cursor_text() == "Ln 1, Col 20"

    window._editor.insertPlainText("\nWHERE 1 = 1")
    assert strip.cursor_text() == "Ln 2, Col 12"
    assert strip.info_text().startswith("2 lines")

    window._cancel_edit_mode()
    assert not strip.is_editing()
    assert strip.cursor_text() == ""


# ── Search chips and scope (redesign F7) ───────────────────────────────────


def _search(win: MainWindow, text: str) -> None:
    win._search_bar._edit.setText(text)
    win._on_search_timeout()


def test_typed_tokens_become_chips_and_still_search(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    window._search_bar.focus()
    QTest.keyClicks(window._search_bar._edit, "table:sales ")
    assert window._search_bar.tokens() == [("table", "sales")]
    window._on_search_timeout()
    assert window._nav.current == NavState("search", library, "table:sales")
    _wait_until(lambda: window._query_list.count() == 1)


def test_the_scope_bar_moves_and_clears_the_search(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    scope = window._scope_bar
    assert scope.isHidden()
    _search(window, "sales")
    assert not scope.isHidden()
    _wait_until(lambda: scope.count_text() == "1 result")
    whole, folder = scope.options()
    assert folder.text() == "library" and folder.isChecked()

    whole.click()
    assert window._nav.current == NavState("search", None, "sales")
    assert whole.isChecked() and not folder.isChecked()
    assert window._search_bar.text() == "sales"

    folder.click()
    assert window._nav.current == NavState("search", library, "sales")

    scope._clear_btn.click()
    assert window._nav.current == NavState("folder", library)
    assert scope.isHidden()
    assert window._search_bar.text() == ""


def test_a_search_groups_by_folder_and_keeps_its_own_grouping(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    assert window._query_list.group_mode() == "date"
    _search(window, "sales")
    assert window._query_list.group_mode() == "folder"
    assert window._command_bar._group == "folder"

    window._on_group_requested("none")
    assert cfg.get_search_group_mode() == "none"
    assert cfg.get_group_mode() == "date"

    window._on_clear_search()
    assert window._query_list.group_mode() == "date"
    assert window._command_bar._group == "date"


def test_a_search_lists_the_closest_match_first_and_keeps_its_own_sort(
    window: MainWindow, tmp_path: Path
) -> None:
    root = tmp_path / "cdc"
    root.mkdir()
    named = root / "Pedidos_CDC.sql"
    named.write_text("SELECT * FROM dbo.Pedidos", encoding="utf-8")
    # Older than the script below, which only names its aliases p_cdc.
    os.utime(named, (1_600_000_000, 1_600_000_000))
    (root / "OPS-90690.sql").write_text(
        "SELECT p_cdc.id FROM pl_cdc p_cdc"
        + " JOIN pl_cdc c_cdc ON c_cdc.id = p_cdc.id" * 15,
        encoding="utf-8",
    )
    window.load_folder(root)
    _wait_until(lambda: window._index_jobs == 0 and window._query_list.count() == 2)
    assert window._query_list.sort_key() == "mtime_desc"

    _search(window, "cdc")
    _wait_until(lambda: window._query_list._results[0].rel_path == "Pedidos_CDC.sql")
    assert window._query_list.sort_key() == "relevance"
    assert window._command_bar._sort == "relevance"
    assert window._command_bar._sort_acts["relevance"].isVisible()

    window._on_sort_requested("name_desc")
    assert cfg.get_search_sort_key() == "name_desc"
    assert cfg.get_sort_key() == "mtime_desc"

    window._on_clear_search()
    assert window._query_list.sort_key() == "mtime_desc"
    assert not window._command_bar._sort_acts["relevance"].isVisible()


def test_the_grid_view_is_chosen_from_the_command_bar_and_remembered(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    selected = window._query_list._current_result()

    window._command_bar._grid.click()

    assert window._query_list.view_mode() == "grid"
    assert window._query_list._stack.currentWidget() is window._query_list._grid
    assert window._command_bar._grid.isChecked()
    assert cfg.get_view_mode() == "grid"
    assert window._query_list._current_result() == selected

    window._command_bar._details.click()
    assert window._query_list.view_mode() == "details"
    assert cfg.get_view_mode() == "details"


def test_the_window_opens_in_the_view_it_was_closed_in(
    window: MainWindow, library: Path
) -> None:
    cfg.set_view_mode("grid")

    second = MainWindow()
    try:
        second._startup_update_timer.stop()
        assert second._query_list.view_mode() == "grid"
        assert second._command_bar._grid.isChecked()
    finally:
        second._quitting = True
        second.close()


def test_a_tag_is_a_chip_and_removing_it_leaves_the_tag(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    window._on_tag_selected("bi")
    assert window._search_bar.tokens() == [("tag", "bi")]
    assert not window._scope_bar.isHidden()
    window._search_bar.chips()[0].remove_btn.click()
    window._on_search_timeout()
    assert window._nav.current == NavState("folder", library)
    assert window._scope_bar.isHidden()


def _open_palette(win: MainWindow) -> CommandPalette:
    QTest.keyClick(win, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
    palette = win._palette
    assert palette is not None and palette.is_open()
    # The library's queries arrive from a worker.
    _wait_until(lambda: palette._loaded)
    return palette


def test_quick_search_opens_a_query_of_the_library(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    window._navigate(NavState("favorites"))
    palette = _open_palette(window)
    kinds = [kind for kind, _value in palette.rows()]
    assert kinds.count("query") == 2 and kinds.count("action") == 3

    QTest.keyClicks(palette._input, "sales")
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    assert window._palette is None
    assert window._nav.current == NavState("folder", library)
    _wait_until(
        lambda: window._query_loaded
        and window._current_result is not None
        and window._current_result.rel_path == "sales.sql"
    )


def test_quick_search_copies_the_sql_of_a_query_not_loaded(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    QApplication.clipboard().clear()
    palette = _open_palette(window)
    QTest.keyClicks(palette._input, "clients")
    QTest.keyClick(palette._input, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert window._palette is None
    _wait_until(lambda: QApplication.clipboard().text() == "SELECT id FROM clients")


def test_quick_search_runs_an_action(
    window: MainWindow, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load(window, library)
    calls: list[str] = []
    monkeypatch.setattr(window, "reindex_library", lambda: calls.append("reindex"))
    palette = _open_palette(window)
    QTest.keyClicks(palette._input, "reindex")
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    assert calls == ["reindex"]


def test_ctrl_p_again_keeps_one_palette_and_ctrl_f_closes_it(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    palette = _open_palette(window)
    QTest.keyClicks(palette._input, "sales")
    QTest.keyClick(palette._input, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
    assert window._palette is palette
    assert palette._input.selectedText() == "sales"

    QTest.keyClick(palette._input, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert window._palette is None
    assert window._search_bar._edit.hasFocus()


def test_quick_search_needs_a_library(window: MainWindow) -> None:
    QTest.keyClick(window, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
    assert window._palette is None


# ---------------------------------------------------------------------------
# The Diagram tab
# ---------------------------------------------------------------------------


def _show_diagram(win: MainWindow, rel_path: str) -> None:
    _select(win, rel_path)
    _wait_until(lambda: win._diagram_pane.state() == "diagram")


def test_the_diagram_tab_draws_the_loaded_query(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _select(window, "sales.sql")
    assert window._diagram_pane.state() == "blank"  # the SQL tab reads nothing

    window._editor_strip._diagram_tab.click()
    assert cfg.get_details_tab() == "diagram"
    assert window._details_stack.currentWidget() is window._diagram_pane
    _wait_until(lambda: window._diagram_pane.state() == "diagram")
    assert window._editor_strip.counts_text() == "1 table"
    zoom = window._diagram_pane.zoom_percent()
    assert window._editor_strip.zoom_text() == f"{zoom}%"
    graph = window._diagram_pane.graph()
    assert graph is not None
    assert {n.name for n in graph.nodes if n.kind == "table"} == {"sales"}

    _show_diagram(window, "reports/clients.sql")
    graph = window._diagram_pane.graph()
    assert graph is not None
    assert {n.name for n in graph.nodes if n.kind == "table"} == {"clients"}

    window._editor_strip._sql_tab.click()
    assert cfg.get_details_tab() == "sql"
    assert window._details_stack.currentWidget() is window._editor


def test_the_diagram_reads_the_declared_dialect(
    window: MainWindow, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_as: list[str | None] = []

    def spy(body: str, dialect: str | None = None) -> QueryGraph:
        read_as.append(dialect)
        return build_query_graph(body, dialect)

    monkeypatch.setattr(mw, "build_query_graph", spy)
    (library / "sales.sql").write_text(
        "/* ---\ntitle: Sales\ndialect: PL/SQL\n--- */\nSELECT * FROM sales",
        encoding="utf-8",
    )
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    _show_diagram(window, "reports/clients.sql")
    # Without a declaration the dialect is left to the body.
    assert read_as == ["oracle", None]


def test_ctrl_shift_d_toggles_the_details_tab(
    window: MainWindow, library: Path
) -> None:
    _load(window, library)
    _select(window, "sales.sql")
    chord = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(window, Qt.Key.Key_D, chord)
    assert window._editor_strip.tab() == "diagram"
    _wait_until(lambda: window._diagram_pane.state() == "diagram")
    QTest.keyClick(window, Qt.Key.Key_D, chord)
    assert window._editor_strip.tab() == "sql"
    assert cfg.get_details_tab() == "sql"


def test_the_window_opens_on_the_saved_tab(window: MainWindow) -> None:
    cfg.set_details_tab("diagram")

    second = MainWindow()
    try:
        second._startup_update_timer.stop()
        assert second._editor_strip.tab() == "diagram"
        assert second._details_stack.currentWidget() is second._diagram_pane
    finally:
        second._quitting = True
        second.close()


def test_a_stale_diagram_only_fills_the_cache(
    window: MainWindow, library: Path
) -> None:
    cfg.set_details_tab("diagram")
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    shown = window._diagram_pane.graph()

    other = build_query_graph("SELECT a FROM elsewhere")
    key = GraphCache.key("SELECT a FROM elsewhere")
    window._on_diagram_ready(other, key, window._diagram_gen - 1)
    assert window._diagram_cache.get(key) is other
    assert window._diagram_pane.graph() is shown


def test_going_back_to_a_query_uses_the_cache(
    window: MainWindow, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    first = window._diagram_pane.graph()
    _show_diagram(window, "reports/clients.sql")

    calls: list[str] = []

    def counting(body: str, dialect: str | None = None) -> QueryGraph:
        calls.append(body)
        return build_query_graph(body, dialect)

    monkeypatch.setattr(mw, "build_query_graph", counting)
    _show_diagram(window, "sales.sql")
    assert window._diagram_pane.graph() is first
    assert window._editor_strip.counts_text() == "1 table"
    assert calls == []


def test_editing_shows_the_sql_but_keeps_the_saved_tab(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")

    window._on_edit_shortcut()
    assert window._edit_mode
    assert window._editor_strip.tab() == "sql"
    assert window._details_stack.currentWidget() is window._editor
    assert cfg.get_details_tab() == "diagram"


@pytest.mark.parametrize("by_key", [False, True])
def test_select_all_from_the_diagram_selects_the_sql(
    window: MainWindow, library: Path, by_key: bool
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")

    if by_key:
        view = window._diagram_pane.view
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    else:
        window._metadata_panel.select_all_requested.emit()
    assert window._editor_strip.tab() == "sql"
    assert window._editor.textCursor().selectedText() == "SELECT * FROM sales"
    assert window._editor.hasFocus()


def test_copy_sql_works_from_the_diagram_tab(window: MainWindow, library: Path) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    window._metadata_panel._copy_btn.click()
    assert QApplication.clipboard().text() == "SELECT * FROM sales"
    assert window._editor_strip.tab() == "diagram"


def test_language_and_theme_reach_the_diagram_without_a_new_analysis(
    window: MainWindow, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    graph = window._diagram_pane.graph()

    calls: list[str] = []
    monkeypatch.setattr(
        mw, "build_query_graph", lambda body, d="tsql": calls.append(body)
    )
    try:
        window._change_language("pt-BR")
        assert window._editor_strip.counts_text() == "1 tabela"
        assert window._editor_strip._diagram_tab.text() == "Diagrama"
        window._apply_theme_live("dark")
        window._apply_theme_live("light")
    finally:
        window._change_language("en")
    assert window._diagram_pane.graph() is graph
    assert window._diagram_pane.state() == "diagram"
    assert calls == []


def _tables(win: MainWindow) -> set[str]:
    graph = win._diagram_pane.displayed()
    assert graph is not None
    return {n.name for n in graph.nodes if n.kind == "table"}


def test_go_to_sql_selects_the_table_in_the_editor(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    go = window._diagram_pane.view.node_menu("sales").actions()[0]
    assert go.text() == "Go to SQL"
    go.trigger()
    assert window._editor_strip.tab() == "sql"
    assert window._details_stack.currentWidget() is window._editor
    assert window._editor.textCursor().selectedText() == "sales"


def test_go_to_line_selects_where_the_query_stops_parsing(
    window: MainWindow, library: Path
) -> None:
    (library / "sales.sql").write_text(
        "/* ---\ntitle: Sales\n--- */\n"
        "SELECT s.id, r.name FROM sales s\n"
        "INNER JOIN stores t ON t.id = s.store_id\n"
        "innerjoin regions r ON r.id = t.region_id\n",
        encoding="utf-8",
    )
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    pane = window._diagram_pane
    assert pane.notices() == ["partial"]
    assert "near “innerjoin” (line 3)" in pane._partial.label.text()

    pane._partial.button.click()
    assert window._editor_strip.tab() == "sql"
    cursor = window._editor.textCursor()
    assert cursor.selectedText() == "innerjoin"
    assert cursor.blockNumber() == 2


def test_search_in_all_queries_from_a_table(window: MainWindow, library: Path) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    menu = window._diagram_pane.view.node_menu("sales")
    search = next(a for a in menu.actions() if a.text() == "Search in all queries")
    search.trigger()
    assert window._nav.current == NavState("search", library, "table:sales")


TWO_STATEMENTS = (
    "SELECT id FROM clients;\n"
    "SELECT o.id FROM orders o JOIN clients c ON c.id = o.client_id;"
)


def test_the_statement_selector_narrows_the_diagram(
    window: MainWindow, library: Path
) -> None:
    (library / "reports" / "clients.sql").write_text(TWO_STATEMENTS, encoding="utf-8")
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "reports/clients.sql")
    strip = window._editor_strip
    assert not strip._statement_button.isHidden()
    assert _tables(window) == {"clients", "orders"}

    actions = [a for a in strip.statement_menu().actions() if not a.isSeparator()]
    actions[1].trigger()
    assert window._diagram_pane.statement() == 1
    assert _tables(window) == {"clients"}
    assert strip.counts_text() == "1 table"
    actions[2].trigger()
    assert _tables(window) == {"clients", "orders"}
    assert strip.counts_text() == "2 tables · 1 join"

    strip.more_actions()["results"].trigger()
    graph = window._diagram_pane.displayed()
    assert graph is not None
    assert all(n.kind != "result" for n in graph.nodes)

    _show_diagram(window, "sales.sql")
    assert strip.current_statement() == 0
    assert window._diagram_pane.statement() == 0
    assert strip._statement_button.isHidden()


def test_editing_redraws_the_diagram_and_keeps_the_last_good_one(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    first = window._diagram_pane.graph()
    window._on_edit_shortcut()
    window._set_details_tab("diagram", save=False)
    scene = window._diagram_pane.view._scene
    scene.move_node("sales", QPointF(40, 30))
    moved = QPointF(scene.moved["sales"])

    window._editor.setPlainText(
        "SELECT * FROM sales s JOIN regions r ON r.id = s.region_id"
    )
    _wait_until(lambda: _tables(window) == {"sales", "regions"})
    assert scene.moved.get("sales") == moved  # the drawing kept its place
    assert not window._editor_strip.is_stale()
    good = window._diagram_pane.graph()

    window._editor.setPlainText("SELECT * FROM sales s JOIN (")
    _wait_until(window._editor_strip.is_stale)
    assert window._diagram_pane.graph() is good

    window._cancel_edit_mode()
    assert not window._editor_strip.is_stale()
    _wait_until(lambda: window._query_loaded and window._diagram_pane.graph() is first)
    assert window._editor.toPlainText() == "SELECT * FROM sales"


def test_a_large_query_waits_for_analyze_anyway(
    window: MainWindow, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mw, "is_large", lambda body: True)
    window._set_details_tab("diagram")
    _load(window, library)
    _select(window, "sales.sql")
    _wait_until(lambda: window._diagram_pane.state() == "large")
    assert window._editor_strip.counts_text() == ""

    window._diagram_pane._large_button.click()
    _wait_until(lambda: window._diagram_pane.state() == "diagram")
    assert _tables(window) == {"sales"}


def _open_window(win: MainWindow) -> DiagramWindow:
    win._editor_strip._expand.click()
    diagram = win._diagram_window
    assert diagram is not None
    _wait_until(lambda: diagram.pane.state() == "diagram")
    return diagram


def _window_tables(diagram: DiagramWindow) -> set[str]:
    graph = diagram.pane.displayed()
    return {n.name for n in graph.nodes if n.kind == "table"} if graph else set()


def test_expand_opens_a_window_that_follows_the_selection(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    diagram = _open_window(window)
    assert diagram.isVisible()
    assert diagram.windowTitle() == "Diagram — sales.sql — SQLShelf"
    assert diagram.title_text() == "Sales"
    assert diagram.path_text() == "library › sales.sql"
    assert diagram.pane.graph() is window._diagram_pane.graph()  # read once

    window._set_details_tab("sql")  # the window follows on its own
    _select(window, "reports/clients.sql")
    assert diagram.windowTitle() == "Diagram — clients.sql — SQLShelf"
    assert diagram.path_text() == "library › reports › clients.sql"
    _wait_until(lambda: _window_tables(diagram) == {"clients"})
    assert window._diagram_pane.state() == "blank"

    window._open_diagram_window()
    assert window._diagram_window is diagram


def test_closing_the_diagram_window_leaves_the_main_window(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    diagram = _open_window(window)
    graph = diagram.pane.graph()
    diagram.close()
    _pump(50)
    assert window.isVisible()
    assert not diagram.isVisible()

    window._open_diagram_window()
    assert diagram.isVisible()
    assert diagram.pane.graph() is graph


def test_go_to_sql_from_the_window_selects_the_table(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    diagram = _open_window(window)
    diagram.pane.view.diagram().select("sales")
    diagram._go.click()
    assert window._editor_strip.tab() == "sql"
    assert window._editor.textCursor().selectedText() == "sales"

    window._set_details_tab("diagram")
    diagram.pane.view.diagram().select(None)
    diagram._go.click()
    assert window._editor_strip.tab() == "sql"


@pytest.mark.parametrize("kind", ["png", "svg"])
def test_an_export_is_written_where_the_user_chooses(
    window: MainWindow,
    library: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    out = tmp_path / "out"
    out.mkdir()
    asked: list[str] = []

    def save_as(parent: object, title: str, suggested: str, filters: str) -> object:
        asked.append(suggested)
        return str(out / "sales"), filters  # no suffix: the kind's is added

    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", save_as)
    window._editor_strip.more_actions()[kind].trigger()
    target = out / f"sales.{kind}"
    assert asked == [str(library / f"sales.diagram.{kind}")]
    _wait_until(lambda: "diagram" in window._status_bar._counts.text().lower())
    assert window._status_bar._counts.text() == f"Diagram saved: {target}"
    head = target.read_bytes()[:256]
    assert head.startswith(b"\x89PNG") if kind == "png" else b"<svg" in head

    diagram = _open_window(window)
    window._export_window_diagram(kind)
    _wait_until(lambda: diagram.counts_text().startswith("Diagram saved"))


def test_a_failed_export_says_why(
    window: MainWindow,
    library: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    missing = tmp_path / "missing" / "sales.svg"
    monkeypatch.setattr(
        mw.QFileDialog,
        "getSaveFileName",
        lambda *args: (str(missing), ""),
    )
    window._editor_strip.more_actions()["svg"].trigger()
    status = window._status_bar._counts
    _wait_until(lambda: status.text().startswith("Could not save the diagram"))
    assert not missing.exists()


def test_a_cancelled_export_writes_nothing(
    window: MainWindow,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    window._editor_strip.more_actions()["png"].trigger()
    _pump(100)
    assert not (library / "sales.diagram.png").exists()
    assert not window._status_bar._counts.text().startswith("Diagram")


def test_copy_image_puts_the_diagram_on_the_clipboard(
    window: MainWindow, library: Path
) -> None:
    window._set_details_tab("diagram")
    _load(window, library)
    _show_diagram(window, "sales.sql")
    QApplication.clipboard().clear()
    window._editor_strip.more_actions()["copy"].trigger()
    image = QApplication.clipboard().image()
    assert not image.isNull()
    assert image.size() == window._diagram_pane.view.render_image().size()
    assert window._status_bar._counts.text() == "Diagram image copied"
