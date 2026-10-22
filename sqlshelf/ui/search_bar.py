from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from ..core.i18n import tr
from . import icons
from .widgets import KbdHint

WIDTH = 320
ACTIVE_WIDTH = 520
FIELD_HEIGHT = 34


class SearchBar(QWidget):
    """Search field of the address bar; emits search_changed as the user types.

    It widens while a search is active and names the place it searches in its
    placeholder. Colours come from ``QLineEdit#SearchInput`` in the global QSS.
    """

    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._location = ""

        self._edit = QLineEdit()
        self._edit.setObjectName("SearchInput")
        self._edit.setFixedHeight(FIELD_HEIGHT)
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.installEventFilter(self)
        self._icon_action = self._edit.addAction(
            icons.icon("search", "text3"), QLineEdit.ActionPosition.LeadingPosition
        )
        self._kbd = KbdHint("Ctrl F", self._edit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit)

        self.setFixedWidth(WIDTH)
        self.retranslate_ui()
        self._update_kbd()

    # ------------------------------------------------------------------

    def retranslate_ui(self) -> None:
        self._edit.setToolTip(tr("search.help"))
        self._update_placeholder()

    def refresh_theme(self) -> None:
        self._icon_action.setIcon(icons.icon("search", "text3"))

    def set_location(self, location: str) -> None:
        """Name the place a search would cover, shown in the placeholder."""
        self._location = location
        self._update_placeholder()

    def set_active(self, active: bool) -> None:
        """Widen the field while a search is active."""
        self.setFixedWidth(ACTIVE_WIDTH if active else WIDTH)

    def text(self) -> str:
        return self._edit.text()

    def set_text_silently(self, text: str) -> None:
        """Show *text* without emitting search_changed."""
        if text == self._edit.text():
            return
        self._edit.blockSignals(True)
        self._edit.setText(text)
        self._edit.blockSignals(False)
        self._update_kbd()

    def clear(self) -> None:
        self._edit.clear()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()

    # ------------------------------------------------------------------

    def _update_placeholder(self) -> None:
        if self._location:
            self._edit.setPlaceholderText(
                tr("search.placeholder_in", location=self._location)
            )
        else:
            self._edit.setPlaceholderText(tr("search.placeholder_short"))

    def _on_text_changed(self, text: str) -> None:
        self._update_kbd()
        self.search_changed.emit(text)

    def _update_kbd(self) -> None:
        """The hint sits where the clear button appears, so it yields to it."""
        empty = not self._edit.text()
        self._kbd.setVisible(empty)
        right = self._kbd.sizeHint().width() + 10 if empty else 0
        self._edit.setTextMargins(0, 0, right, 0)
        self._place_kbd()

    def _place_kbd(self) -> None:
        hint = self._kbd.sizeHint()
        self._kbd.resize(hint)
        self._kbd.move(
            self._edit.width() - hint.width() - 8,
            (self._edit.height() - hint.height()) // 2,
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._edit and event.type() == QEvent.Type.Resize:
            self._place_kbd()
        return super().eventFilter(watched, event)
