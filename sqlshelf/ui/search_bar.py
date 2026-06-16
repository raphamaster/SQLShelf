from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from ..core.i18n import tr
from .theme import tokens as _tk
from .theme.tokens import (
    ACCENT,
    ACCENT_BORDER,
    ACCENT_FILL,
    ACCENT_FOCUS_BG,
    RADIUS,
    SELECTION_BG,
    TEXT_PRIMARY,
)

# Style is applied to the QLineEdit itself — reliable even with qt-material overrides.
_STYLESHEET = f"""
    QLineEdit#SearchInput {{
        background-color: {ACCENT_FILL};
        border: 1px solid {ACCENT_BORDER};
        border-radius: {RADIUS}px;
        color: {TEXT_PRIMARY};
        font-size: 13px;
        min-height: 44px;
        selection-background-color: {SELECTION_BG};
    }}
    QLineEdit#SearchInput:focus {{
        background-color: {ACCENT_FOCUS_BG};
        border: 2px solid {ACCENT};
    }}
"""


def _make_search_icon() -> QIcon:
    """Draw a minimal magnifying glass icon in the accent colour."""
    size = 16
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(_tk.ACCENT)  # reads live value after set_active_palette()
    col.setAlpha(160)
    pen = QPen(col, 1.8)
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
        self._edit.setPlaceholderText(tr("search.placeholder"))
        self._edit.setToolTip(tr("search.help"))
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.search_changed)
        # Magnifying glass icon lives inside the QLineEdit (native Qt feature).
        self._icon_action = self._edit.addAction(
            _make_search_icon(), QLineEdit.ActionPosition.LeadingPosition
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit, stretch=1)

        self.setStyleSheet(_STYLESHEET)

    def retranslate_ui(self) -> None:
        self._edit.setPlaceholderText(tr("search.placeholder"))
        self._edit.setToolTip(tr("search.help"))

    def refresh_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QLineEdit#SearchInput {{
                background-color: {_tk.ACCENT_FILL};
                border: 1px solid {_tk.ACCENT_BORDER};
                border-radius: {_tk.RADIUS}px;
                color: {_tk.TEXT_PRIMARY};
                font-size: 13px;
                min-height: 44px;
                selection-background-color: {_tk.SELECTION_BG};
            }}
            QLineEdit#SearchInput:focus {{
                background-color: {_tk.ACCENT_FOCUS_BG};
                border: 2px solid {_tk.ACCENT};
            }}
            """
        )
        self._edit.removeAction(self._icon_action)
        self._icon_action = self._edit.addAction(
            _make_search_icon(), QLineEdit.ActionPosition.LeadingPosition
        )

    def text(self) -> str:
        return self._edit.text()

    def clear(self) -> None:
        self._edit.clear()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()
