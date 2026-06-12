from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget


class SearchBar(QWidget):
    """Search input that emits search_changed when the user types."""

    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Search… (table:X  col:X  tag:X  or free text)")
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.search_changed)

        clear_btn = QPushButton("✕")
        clear_btn.setFixedWidth(28)
        clear_btn.setToolTip("Clear search")
        clear_btn.clicked.connect(self.clear)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit)

    def text(self) -> str:
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()
