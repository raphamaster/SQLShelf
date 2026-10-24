from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sqlshelf.core import config as cfg
from sqlshelf.core.frontmatter import read_sql_file
from sqlshelf.core.i18n import set_language
from sqlshelf.core.navigation import NavState
from sqlshelf.ui import main_window as mw
from sqlshelf.ui.command_palette import CommandPalette
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
    assert window._editor_strip.info_text() == "1 line · UTF-8 · T-SQL"
    assert panel._title_label.full_text() == "Sales"
    assert panel._file_name_label.full_text() == "sales.sql"
    assert panel._copy_btn.isEnabled()
    assert panel._edit_btn.isEnabled()


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
    assert body == "SELECT * FROM sales"
    assert panel._title_label.full_text() == "Monthly sales"


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
