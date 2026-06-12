from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

SQL_KEYWORDS = [
    "SELECT", "FROM", "WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "OUTER",
    "FULL", "ON", "GROUP", "BY", "ORDER", "HAVING", "INSERT", "INTO",
    "VALUES", "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "ALTER",
    "DROP", "AS", "AND", "OR", "NOT", "NULL", "IS", "IN", "EXISTS",
    "BETWEEN", "LIKE", "TOP", "DISTINCT", "UNION", "ALL", "CASE", "WHEN",
    "THEN", "ELSE", "END", "WITH", "CROSS", "APPLY", "OVER", "PARTITION",
    "DECLARE", "EXEC", "EXECUTE", "USE", "ASC", "DESC", "PRIMARY", "KEY",
    "FOREIGN", "REFERENCES", "DEFAULT", "CHECK",
]


class SqlHighlighter(QSyntaxHighlighter):
    """Highlighter simples por regex — suficiente para o esqueleto.

    Será substituído/complementado por sqlglot na Fase 1 do Core
    (extração de tabelas/colunas), mas o highlight visual continua
    sendo regex puro mesmo depois.
    """

    def __init__(self, document):
        super().__init__(document)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#569CD6"))
        keyword_format.setFontWeight(QFont.Weight.Bold)
        for word in SQL_KEYWORDS:
            pattern = QRegularExpression(
                rf"\b{word}\b",
                QRegularExpression.PatternOption.CaseInsensitiveOption,
            )
            self._rules.append((pattern, keyword_format))

        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#CE9178"))
        self._rules.append((QRegularExpression(r"'[^']*'"), string_format))

        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#B5CEA8"))
        self._rules.append((QRegularExpression(r"\b\d+\b"), number_format))

        self._comment_format = QTextCharFormat()
        self._comment_format.setForeground(QColor("#6A9955"))
        self._rules.append((QRegularExpression(r"--[^\n]*"), self._comment_format))

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
