from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtWidgets import QApplication

from sqlshelf.ui import tokens as _tk
from sqlshelf.ui.highlighter import SqlHighlighter


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _colours(text: str, dialect: str) -> list[str | None]:
    """The colour of each character of *text*, None where it has none."""
    _app()
    document = QTextDocument()
    document.setPlainText(text)
    highlighter = SqlHighlighter(document, dialect)
    highlighter.rehighlight()
    block = document.firstBlock()
    colours: list[str | None] = [None] * len(text)
    for found in block.layout().formats():
        brush = found.format.foreground()
        for i in range(found.start, found.start + found.length):
            colours[i] = brush.color().name() if brush.style() else None
    return colours


def _token(name: str) -> str:
    return QColor(_tk.color(f"syn_{name}")).name()


def _colour_of(text: str, part: str, dialect: str) -> set[str | None]:
    start = text.index(part)
    return set(_colours(text, dialect)[start : start + len(part)])


@pytest.mark.parametrize(
    ("text", "part", "dialect", "token"),
    [
        # "#" is a comment in MySQL and BigQuery, a temporary table in T-SQL.
        ("SELECT 1 # note", "# note", "mysql", "comment"),
        ("SELECT 1 # note", "# note", "bigquery", "comment"),
        ("SELECT * FROM #temp", "#temp", "tsql", None),
        # "…" is a string in MySQL, a name elsewhere.
        ('SELECT "order" FROM t', '"order"', "mysql", "string"),
        ('SELECT "order" FROM t', '"order"', "postgres", "identifier"),
        ('SELECT "order" FROM t', '"order"', "oracle", "identifier"),
        # A quoted name hides the keyword it spells.
        ("SELECT `order` FROM t", "`order`", "mysql", "identifier"),
        ("SELECT `order` FROM t", "`order`", "bigquery", "identifier"),
        ("SELECT [order] FROM t", "[order]", "tsql", "identifier"),
        ("SELECT [order] FROM t", "[order]", "sql", "identifier"),
        # Oracle's q'[…]' holds a quote.
        ("SELECT q'[it's]' FROM dual", "q'[it's]'", "oracle", "string"),
        # Each dialect's own words.
        ("SELECT * FROM t LIMIT 5", "LIMIT", "postgres", "keyword"),
        ("SELECT * FROM t LIMIT 5", "LIMIT", "mysql", "keyword"),
        ("SELECT * FROM t QUALIFY x", "QUALIFY", "bigquery", "keyword"),
        ("SELECT * FROM t CONNECT BY x", "CONNECT", "oracle", "keyword"),
        ("SELECT * FROM t LIMIT 5", "LIMIT", "tsql", None),
        ("GO", "GO", "tsql", "keyword"),
    ],
)
def test_each_dialect_has_its_own_syntax(
    text: str, part: str, dialect: str, token: str | None
) -> None:
    expected = None if token is None else _token(token)
    if token == "identifier":
        # Drawn as a name: no keyword colour inside.
        assert _token("keyword") not in _colour_of(text, part, dialect)
    else:
        assert _colour_of(text, part, dialect) == {expected}


def test_an_unknown_dialect_is_coloured_as_tsql() -> None:
    text = "SELECT [order] FROM #t"
    assert _colours(text, "snowflake") == _colours(text, "tsql")


def test_set_dialect_recolours_the_text() -> None:
    _app()
    document = QTextDocument()
    document.setPlainText("SELECT 1 # note")
    highlighter = SqlHighlighter(document)
    highlighter.rehighlight()
    assert highlighter.dialect() == "sql"

    highlighter.set_dialect("mysql")
    assert highlighter.dialect() == "mysql"
    formats = document.firstBlock().layout().formats()
    comment = _token("comment")
    assert any(
        f.format.foreground().color().name() == comment and f.start == 9
        for f in formats
    )
