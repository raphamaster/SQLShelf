"""The thin bar above the SQL editor: "SQL" and the file's line count and encoding.

While the metadata is edited it also says the SQL is editable and where the
cursor is ("Ln 10, Col 27").
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..core.i18n import ntr, tr

STRIP_HEIGHT = 32
DIALECT = "T-SQL"


def strip_info(lines: int, encoding: str) -> str:
    """ "12 lines · UTF-8 · T-SQL", leaving the encoding out when unknown."""
    parts = [ntr("editor.lines_one", "editor.lines_other", lines)]
    if encoding:
        parts.append(encoding)
    parts.append(DIALECT)
    return " · ".join(parts)


class EditorStrip(QWidget):
    """Title and file facts of the query in the editor; empty until one loads."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(STRIP_HEIGHT)
        self._lines: int | None = None
        self._encoding = ""
        self._cursor_pos: tuple[int, int] | None = None

        self._title = QLabel()
        self._title.setObjectName("EditorStripTitle")
        self._info = QLabel()
        self._info.setObjectName("EditorStripInfo")
        self._mode = QLabel()
        self._mode.setObjectName("EditorStripMode")
        self._mode.hide()
        self._cursor = QLabel()
        self._cursor.setObjectName("EditorStripInfo")
        self._cursor.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title)
        layout.addWidget(self._info)
        layout.addWidget(self._mode)
        layout.addStretch(1)
        layout.addWidget(self._cursor)

        self.retranslate_ui()

    def set_info(self, lines: int, encoding: str) -> None:
        self._lines = lines
        self._encoding = encoding
        self._info.setText(strip_info(lines, encoding))

    def set_lines(self, lines: int) -> None:
        """The line count changed while editing; the encoding stays."""
        self.set_info(lines, self._encoding)

    def set_editing(self, editing: bool) -> None:
        """Show "editable" and the cursor position, or hide them again."""
        self._mode.setVisible(editing)
        self._cursor.setVisible(editing)
        if not editing:
            self._cursor_pos = None
            self._cursor.clear()

    def is_editing(self) -> bool:
        return not self._mode.isHidden()

    def set_cursor(self, line: int, column: int) -> None:
        self._cursor_pos = (line, column)
        self._cursor.setText(tr("editor.cursor", line=line, col=column))

    def clear(self) -> None:
        self._lines = None
        self._encoding = ""
        self._info.clear()
        self.set_editing(False)

    def info_text(self) -> str:
        return self._info.text()

    def mode_text(self) -> str:
        return self._mode.text() if self.is_editing() else ""

    def cursor_text(self) -> str:
        return self._cursor.text() if self.is_editing() else ""

    def retranslate_ui(self) -> None:
        self._title.setText(tr("editor.strip_title"))
        self._mode.setText(tr("editor.editable"))
        if self._lines is not None:
            self._info.setText(strip_info(self._lines, self._encoding))
        if self._cursor_pos is not None:
            self.set_cursor(*self._cursor_pos)
