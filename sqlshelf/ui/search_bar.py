from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from .theme.tokens import ACCENT, ACCENT_BORDER, ACCENT_FILL, RADIUS

_HELP_TOOLTIP = (
    "Search operators:\n"
    "  table:name   queries referencing a table\n"
    "  col:name     queries referencing a column\n"
    "  tag:name     queries tagged with name\n"
    "  free text    full-text search across title & body\n\n"
    "Tip: combine operators, e.g.  table:orders  tag:report"
)

# Style is applied to the QLineEdit itself — reliable even with qt-material overrides.
_STYLESHEET = f"""
    QLineEdit#SearchInput {{
        background-color: {ACCENT_FILL};
        border: 1px solid {ACCENT_BORDER};
        border-radius: {RADIUS}px;
        color: rgba(255,255,255,0.92);
        font-size: 13px;
        min-height: 44px;
        selection-background-color: #264F78;
    }}
    QLineEdit#SearchInput:focus {{
        background-color: rgba(10,222,153,0.15);
        border: 2px solid {ACCENT};
    }}
    QPushButton#SearchHelpBtn {{
        color: rgba(255,255,255,0.38);
        background: transparent;
        border: none;
        border-radius: 3px;
        padding: 2px 4px;
    }}
    QPushButton#SearchHelpBtn:hover {{
        color: rgba(255,255,255,0.92);
        background-color: rgba(255,255,255,0.06);
    }}
"""


def _make_search_icon() -> QIcon:
    """Draw a minimal magnifying glass icon in the accent colour."""
    size = 16
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(10, 222, 153, 160), 1.8)   # ACCENT at ~63 % opacity
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(1.5, 1.5, 9.0, 9.0))        # lens
    p.drawLine(QPointF(9.5, 9.5), QPointF(14.0, 14.0))  # handle
    p.end()
    return QIcon(pix)


class SearchBar(QWidget):
    """Search input that emits search_changed when the user types."""

    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._edit = QLineEdit()
        self._edit.setObjectName("SearchInput")
        self._edit.setPlaceholderText(
            "Search queries — or try  table:X · col:X · tag:X"
        )
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.search_changed)
        # Magnifying glass icon lives inside the QLineEdit (native Qt feature).
        self._edit.addAction(_make_search_icon(), QLineEdit.ActionPosition.LeadingPosition)

        help_btn = QPushButton("?")
        help_btn.setObjectName("SearchHelpBtn")
        help_btn.setFixedWidth(24)
        help_btn.setFlat(True)
        help_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        help_btn.setToolTip(_HELP_TOOLTIP)
        help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._edit, stretch=1)
        layout.addWidget(help_btn)

        self.setStyleSheet(_STYLESHEET)

    def text(self) -> str:
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()
