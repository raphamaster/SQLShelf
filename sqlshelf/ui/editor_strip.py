"""The thin bar above the SQL editor: "SQL" and the file's line count and encoding."""

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

        self._title = QLabel()
        self._title.setObjectName("EditorStripTitle")
        self._info = QLabel()
        self._info.setObjectName("EditorStripInfo")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title)
        layout.addWidget(self._info)
        layout.addStretch(1)

        self.retranslate_ui()

    def set_info(self, lines: int, encoding: str) -> None:
        self._lines = lines
        self._encoding = encoding
        self._info.setText(strip_info(lines, encoding))

    def clear(self) -> None:
        self._lines = None
        self._encoding = ""
        self._info.clear()

    def info_text(self) -> str:
        return self._info.text()

    def retranslate_ui(self) -> None:
        self._title.setText(tr("editor.strip_title"))
        if self._lines is not None:
            self._info.setText(strip_info(self._lines, self._encoding))
