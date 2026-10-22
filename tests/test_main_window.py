from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sqlshelf.core import config as cfg
from sqlshelf.core.i18n import set_language
from sqlshelf.core.navigation import NavState
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
