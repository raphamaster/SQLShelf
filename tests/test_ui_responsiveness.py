from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sqlshelf.core.models import SearchResult
from sqlshelf.ui.code_editor import CodeEditor
from sqlshelf.ui.highlighter import SqlHighlighter
from sqlshelf.ui.query_list import QueryListWidget

_APP: QApplication | None = None


def _app() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
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
