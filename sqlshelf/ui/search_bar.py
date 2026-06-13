from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

_HELP_TOOLTIP = (
    "Search operators:\n"
    "  table:name   queries referencing a table\n"
    "  col:name     queries referencing a column\n"
    "  tag:name     queries tagged with name\n"
    "  free text    full-text search across title & body\n\n"
    "Tip: combine operators, e.g.  table:orders  tag:report"
)


class SearchBar(QWidget):
    """Search input that emits search_changed when the user types."""

    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Search… (table:X  col:X  tag:X  or free text)")
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.search_changed)

        help_btn = QPushButton("?")
        help_btn.setObjectName("SearchHelpBtn")
        help_btn.setFixedWidth(22)
        help_btn.setFlat(True)
        help_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        help_btn.setToolTip(_HELP_TOOLTIP)
        help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit)
        layout.addWidget(help_btn)

    def text(self) -> str:
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()
