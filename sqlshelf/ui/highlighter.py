"""SQL colouring for the editor and the thumbnails, in the query's dialect.

Every dialect shares ``SQL_KEYWORDS``; each adds its own words, comments,
strings and quoted names: ``#`` starts a comment in MySQL and BigQuery but a
temporary table in T-SQL, and ``"x"`` is a string in MySQL but a name in
PostgreSQL. A quoted name is drawn as a name, so ``[order]`` shows no keyword.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)

from ..core.dialects import GENERIC
from . import tokens as _tk

SQL_KEYWORDS = [
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "INNER",
    "LEFT",
    "RIGHT",
    "OUTER",
    "FULL",
    "ON",
    "GROUP",
    "BY",
    "ORDER",
    "HAVING",
    "INSERT",
    "INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE",
    "CREATE",
    "TABLE",
    "ALTER",
    "DROP",
    "AS",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "IS",
    "IN",
    "EXISTS",
    "BETWEEN",
    "LIKE",
    "TOP",
    "DISTINCT",
    "UNION",
    "ALL",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "WITH",
    "CROSS",
    "APPLY",
    "OVER",
    "PARTITION",
    "DECLARE",
    "EXEC",
    "EXECUTE",
    "USE",
    "ASC",
    "DESC",
    "PRIMARY",
    "KEY",
    "FOREIGN",
    "REFERENCES",
    "DEFAULT",
    "CHECK",
]

_BRACKETS = r"\[[^\]]*\]"
_DOUBLE_QUOTES = r'"[^"]*"'
_BACKTICKS = r"`[^`]*`"
_HASH_COMMENT = r"#[^\n]*"
# Oracle's q'[…]' may hold quotes; its brackets end it.
_Q_QUOTE = r"[nN]?[qQ]'(?:\[.*?\]|\{.*?\}|\(.*?\)|<.*?>|(\S).*?\1)'"


@dataclass(frozen=True)
class _Syntax:
    keywords: tuple[str, ...] = ()
    names: tuple[str, ...] = ()  # quoted names
    strings: tuple[str, ...] = ()  # besides '…'
    comments: tuple[str, ...] = ()  # besides -- and /* */


_TSQL = _Syntax(
    keywords=("GO", "BEGIN", "IF", "WHILE", "RETURN", "PRINT", "MERGE", "USING"),
    names=(_BRACKETS, _DOUBLE_QUOTES),
)
_MYSQL = _Syntax(
    keywords=(
        "LIMIT",
        "OFFSET",
        "BEGIN",
        "IF",
        "WHILE",
        "LOOP",
        "RETURN",
        "RETURNS",
        "FUNCTION",
        "PROCEDURE",
        "DELIMITER",
        "REPLACE",
        "IGNORE",
        "DUPLICATE",
    ),
    names=(_BACKTICKS,),
    strings=(_DOUBLE_QUOTES,),
    comments=(_HASH_COMMENT,),
)
_SYNTAX: dict[str, _Syntax] = {
    GENERIC.key: _TSQL,
    "tsql": _TSQL,
    "postgres": _Syntax(
        keywords=(
            "LIMIT",
            "OFFSET",
            "FETCH",
            "ONLY",
            "RETURNING",
            "ILIKE",
            "LATERAL",
            "BEGIN",
            "DO",
            "IF",
            "LOOP",
            "RETURN",
            "RETURNS",
            "FUNCTION",
            "LANGUAGE",
            "CONFLICT",
        ),
        names=(_DOUBLE_QUOTES,),
    ),
    "mysql": _MYSQL,
    "mariadb": _MYSQL,
    "oracle": _Syntax(
        keywords=(
            "BEGIN",
            "IF",
            "ELSIF",
            "LOOP",
            "RETURN",
            "EXCEPTION",
            "CURSOR",
            "FUNCTION",
            "PROCEDURE",
            "PACKAGE",
            "BODY",
            "MERGE",
            "USING",
            "CONNECT",
            "PRIOR",
            "START",
            "MINUS",
            "FETCH",
            "ONLY",
        ),
        names=(_DOUBLE_QUOTES,),
        strings=(_Q_QUOTE,),
    ),
    "bigquery": _Syntax(
        keywords=(
            "LIMIT",
            "OFFSET",
            "QUALIFY",
            "STRUCT",
            "ARRAY",
            "UNNEST",
            "EXCEPT",
            "REPLACE",
            "WINDOW",
            "BEGIN",
            "IF",
            "LOOP",
            "RETURN",
            "MERGE",
            "USING",
        ),
        names=(_BACKTICKS,),
        strings=(_DOUBLE_QUOTES,),
        comments=(_HASH_COMMENT,),
    ),
}


class SqlHighlighter(QSyntaxHighlighter):
    """*dialect* is a key of ``dialects.DIALECTS``; anything else is plain SQL,
    coloured as T-SQL."""

    def __init__(self, document: QTextDocument, dialect: str = GENERIC.key) -> None:
        super().__init__(document)
        self._dialect = dialect
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build_rules()

    def dialect(self) -> str:
        return self._dialect

    def set_dialect(self, dialect: str) -> None:
        """Colour the text as *dialect*; the same dialect again changes nothing."""
        if dialect == self._dialect:
            return
        self._dialect = dialect
        self._build_rules()
        self.rehighlight()

    def _build_rules(self) -> None:
        # Later rules paint over earlier ones: a keyword or a number inside a
        # name, a string or a comment takes its colour.
        syntax = _SYNTAX.get(self._dialect, _TSQL)
        self._rules = []

        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor(_tk.color("syn_keyword")))
        keyword_format.setFontWeight(QFont.Weight.Medium)
        # One alternation instead of one QRegularExpression per keyword: highlightBlock()
        # runs per line, so 60+ separate regex scans per line made loading large files slow.
        keyword_alt = "|".join(dict.fromkeys([*SQL_KEYWORDS, *syntax.keywords]))
        keyword_pattern = QRegularExpression(
            rf"\b(?:{keyword_alt})\b",
            QRegularExpression.PatternOption.CaseInsensitiveOption,
        )
        self._rules.append((keyword_pattern, keyword_format))

        number_format = QTextCharFormat()
        number_format.setForeground(QColor(_tk.color("syn_number")))
        self._rules.append((QRegularExpression(r"\b\d+\b"), number_format))

        name_format = QTextCharFormat()
        name_format.setForeground(QColor(_tk.color("syn_identifier")))
        for pattern in syntax.names:
            self._rules.append((QRegularExpression(pattern), name_format))

        string_format = QTextCharFormat()
        string_format.setForeground(QColor(_tk.color("syn_string")))
        for pattern in ("'[^']*'", *syntax.strings):
            self._rules.append((QRegularExpression(pattern), string_format))

        self._comment_format = QTextCharFormat()
        self._comment_format.setForeground(QColor(_tk.color("syn_comment")))
        self._comment_format.setFontItalic(True)
        for pattern in (r"--[^\n]*", *syntax.comments):
            self._rules.append((QRegularExpression(pattern), self._comment_format))

    def refresh_theme(self) -> None:
        self._build_rules()
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

        # Comentário em bloco /* ... */ multi-linha
        self.setCurrentBlockState(0)
        start = 0
        if self.previousBlockState() != 1:
            start = text.find("/*")

        while start >= 0:
            end = text.find("*/", start)
            if end == -1:
                self.setCurrentBlockState(1)
                length = len(text) - start
            else:
                length = end - start + 2
            self.setFormat(start, length, self._comment_format)
            start = text.find("/*", start + length)
