from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from sqlshelf.core import config as cfg
from sqlshelf.core.i18n import set_language
from sqlshelf.core.models import SearchResult
from sqlshelf.core.query_graph import QueryGraph, build_query_graph
from sqlshelf.ui import main_window as mw
from sqlshelf.ui.code_editor import CodeEditor
from sqlshelf.ui.highlighter import SqlHighlighter
from sqlshelf.ui.query_list import QueryListWidget

_APP: QApplication | None = None


def _app() -> QApplication:
    global _APP
    # instance() is typed as the QCoreApplication base; anything a widget test
    # needs lives on QApplication, so an existing non-widget app is not reusable.
    existing = QApplication.instance()
    _APP = existing if isinstance(existing, QApplication) else QApplication([])
    return _APP


def _result(
    rel_path: str, folder: str = "project", file_mtime: int = 0
) -> SearchResult:
    return SearchResult(
        query_id=1,
        rel_path=rel_path,
        title=Path(rel_path).stem,
        description="",
        snippet="",
        rank=0.0,
        folder=Path(folder),
        file_mtime=file_mtime,
    )


def test_large_document_suspends_expensive_highlighting() -> None:
    _app()
    editor = CodeEditor()
    highlighter = SqlHighlighter(editor.document())
    editor.set_syntax_highlighter(highlighter)

    editor.setPlainText("SELECT 1;\n" * 1_100)

    assert highlighter.document() is None
    assert editor.blockCount() == 1_101


def test_small_document_restores_highlighting_after_large_document() -> None:
    _app()
    editor = CodeEditor()
    highlighter = SqlHighlighter(editor.document())
    editor.set_syntax_highlighter(highlighter)
    editor.setPlainText("SELECT 1;\n" * 1_100)

    editor.setPlainText("SELECT * FROM users")

    assert highlighter.document() is editor.document()


def test_result_refresh_does_not_reemit_unchanged_selection() -> None:
    _app()
    widget = QueryListWidget()
    selected: list[SearchResult] = []
    widget.query_selected.connect(selected.append)
    first = _result("a.sql")
    second = _result("b.sql")

    widget.set_results([first, second])
    widget.set_results([_result("a.sql"), _result("b.sql")])

    assert [result.rel_path for result in selected] == ["a.sql"]


def test_result_identity_includes_folder() -> None:
    _app()
    widget = QueryListWidget()
    selected: list[SearchResult] = []
    widget.query_selected.connect(selected.append)

    widget.set_results([_result("same.sql", "one")])
    widget.set_results([_result("same.sql", "two")])

    assert [result.folder for result in selected] == [Path("one"), Path("two")]


def test_default_sort_is_most_recently_modified() -> None:
    _app()
    widget = QueryListWidget()
    selected: list[SearchResult] = []
    widget.query_selected.connect(selected.append)

    widget.set_results(
        [
            _result("older.sql", file_mtime=100),
            _result("newest.sql", file_mtime=300),
            _result("middle.sql", file_mtime=200),
        ]
    )

    assert [result.rel_path for result in widget._results] == [
        "newest.sql",
        "middle.sql",
        "older.sql",
    ]
    assert selected[0].rel_path == "newest.sql"


def _pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_fast_navigation_with_the_diagram_tab_never_parses_on_the_gui_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app()
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cfg, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfg, "_CONFIG_FILE", config_dir / "config.json")
    cfg.set_details_tab("diagram")
    set_language("en")
    library = tmp_path / "library"
    library.mkdir()
    names = [f"q{i:02}.sql" for i in range(12)]
    for i, name in enumerate(names):
        (library / name).write_text(
            f"SELECT a.id, b.v FROM t{i} a JOIN u{i} b ON b.id = a.id",
            encoding="utf-8",
        )

    on_gui_thread: list[bool] = []

    def recording(body: str, dialect: str = "tsql") -> QueryGraph:
        on_gui_thread.append(threading.current_thread() is threading.main_thread())
        time.sleep(0.05)  # a long script
        return build_query_graph(body, dialect)

    monkeypatch.setattr(mw, "build_query_graph", recording)

    win = mw.MainWindow()
    win._startup_update_timer.stop()
    win.show()
    try:
        win.load_folder(library)
        deadline = time.monotonic() + 20
        while win._index_jobs or win._query_list.count() < 12:
            assert time.monotonic() < deadline, "the library did not load"
            _pump(20)

        for name in names:
            win._query_list.select_by_rel_path(name)
            _pump(30)  # holding the arrow key down

        deadline = time.monotonic() + 10
        while win._diagram_pane.state() != "diagram":
            assert time.monotonic() < deadline, "the last query was not drawn"
            _pump(20)
        graph = win._diagram_pane.graph()
        assert graph is not None
        assert {n.name for n in graph.nodes if n.kind == "table"} == {"t11", "u11"}
        assert win._editor_strip.counts_text() == "2 tables · 1 join"
        assert on_gui_thread and not any(on_gui_thread)
        assert len(on_gui_thread) < len(names)
    finally:
        win._quitting = True
        win.close()
        app.processEvents()
