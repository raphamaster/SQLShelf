from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from .theme.tokens import ACCENT_BORDER, BORDER_EMPH, CARD, RADIUS, TEXT_SECONDARY

_HELP_TOOLTIP = (
    "Search operators:\n"
    "  table:name   queries referencing a table\n"
    "  col:name     queries referencing a column\n"
    "  tag:name     queries tagged with name\n"
    "  free text    full-text search across title & body\n\n"
    "Tip: combine operators, e.g.  table:orders  tag:report"
)

_STYLE_NORMAL = f"""
    QWidget#SearchBar {{
        background-color: {CARD};
        border: 1px solid {BORDER_EMPH};
        border-radius: {RADIUS}px;
    }}
    QWidget#SearchBar QLineEdit {{
        background: transparent;
        border: none;
        color: rgba(255,255,255,0.92);
        font-size: 13px;
        min-height: 36px;
        selection-background-color: #264F78;
    }}
    QLabel#SearchIcon {{
        font-size: 15px;
        color: {TEXT_SECONDARY};
        background: transparent;
    }}
"""

_STYLE_FOCUSED = f"""
    QWidget#SearchBar {{
        background-color: {CARD};
        border: 1px solid {ACCENT_BORDER};
        border-radius: {RADIUS}px;
    }}
    QWidget#SearchBar QLineEdit {{
        background: transparent;
        border: none;
        color: rgba(255,255,255,0.92);
        font-size: 13px;
        min-height: 36px;
        selection-background-color: #264F78;
    }}
    QLabel#SearchIcon {{
        font-size: 15px;
        color: rgba(10,222,153,0.70);
        background: transparent;
    }}
"""


class SearchBar(QWidget):
    """Search input that emits search_changed when the user types."""

    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchBar")
        self.setMinimumHeight(42)

        icon = QLabel("🔍")
        icon.setObjectName("SearchIcon")
        icon.setFixedWidth(26)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._edit = QLineEdit()
        self._edit.setObjectName("SearchInput")
        self._edit.setPlaceholderText(
            "Search queries — or try  table:X · col:X · tag:X"
        )
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.search_changed)
        self._edit.installEventFilter(self)

        help_btn = QPushButton("?")
        help_btn.setObjectName("SearchHelpBtn")
        help_btn.setFixedWidth(22)
        help_btn.setFlat(True)
        help_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        help_btn.setToolTip(_HELP_TOOLTIP)
        help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(4)
        layout.addWidget(icon)
        layout.addWidget(self._edit, stretch=1)
        layout.addWidget(help_btn)

        self.setStyleSheet(_STYLE_NORMAL)

    # ------------------------------------------------------------------
    # Focus tracking
    # ------------------------------------------------------------------

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self._edit:
            if event.type() == QEvent.Type.FocusIn:
                self.setStyleSheet(_STYLE_FOCUSED)
            elif event.type() == QEvent.Type.FocusOut:
                self.setStyleSheet(_STYLE_NORMAL)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def text(self) -> str:
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()
