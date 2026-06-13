from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from ..core.i18n import tr
from .theme import tokens as _tk
from .theme.tokens import (
    ACCENT,
    ACCENT_BORDER,
    ACCENT_FILL,
    ACCENT_FOCUS_BG,
    HOVER_BG_MEDIUM,
    RADIUS,
    SELECTION_BG,
    TEXT_PRIMARY,
    TEXT_TERTIARY,
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
    QPushButton#SearchHelpBtn {{
        color: {TEXT_TERTIARY};
        background: transparent;
        border: none;
        border-radius: 3px;
        padding: 2px 4px;
    }}
    QPushButton#SearchHelpBtn:hover {{
        color: {TEXT_PRIMARY};
        background-color: {HOVER_BG_MEDIUM};
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
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.search_changed)
        # Magnifying glass icon lives inside the QLineEdit (native Qt feature).
        self._icon_action = self._edit.addAction(
            _make_search_icon(), QLineEdit.ActionPosition.LeadingPosition
        )

        self._help_btn = QPushButton("?")
        self._help_btn.setObjectName("SearchHelpBtn")
        self._help_btn.setFixedWidth(24)
        self._help_btn.setFlat(True)
        self._help_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._help_btn.setToolTip(tr("search.help"))
        self._help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._edit, stretch=1)
        layout.addWidget(self._help_btn)

        self.setStyleSheet(_STYLESHEET)

    def retranslate_ui(self) -> None:
        self._edit.setPlaceholderText(tr("search.placeholder"))
        self._help_btn.setToolTip(tr("search.help"))

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
            QPushButton#SearchHelpBtn {{
                color: {_tk.TEXT_TERTIARY};
                background: transparent;
                border: none;
                border-radius: 3px;
                padding: 2px 4px;
            }}
            QPushButton#SearchHelpBtn:hover {{
                color: {_tk.TEXT_PRIMARY};
                background-color: {_tk.HOVER_BG_MEDIUM};
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
