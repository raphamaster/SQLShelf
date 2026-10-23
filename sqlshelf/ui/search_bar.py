"""The address bar's search field: ``op:value`` tokens as chips, then free text.

A token followed by a space, Enter or Tab becomes a chip ("table: Pedidos ×");
its × removes it and Backspace in the empty input removes the last one. What
the field searches is the chips and the free text joined back into one string
(core: ``join_tokens``), so a search means exactly what typing it all would.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from ..core.i18n import tr
from ..core.search import join_tokens, split_tokens
from . import icons
from .widgets import KbdHint

WIDTH = 320
ACTIVE_WIDTH = 520
FIELD_HEIGHT = 34
CHIP_HEIGHT = 24
CHIP_VALUE_MAX = 140
_INPUT_MIN_WIDTH = 60
_LAST_SPACE_RE = re.compile(r"\s(?=\S*$)")


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class _ElidedLabel(QLabel):
    """A label that shortens its text with "…" when the field runs out of room."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def full_text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:
        width = self.fontMetrics().horizontalAdvance(self._full) + 2
        return QSize(min(width, CHIP_VALUE_MAX), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        width = min(self.sizeHint().width(), 24)
        return QSize(width, super().minimumSizeHint().height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        metrics = self.fontMetrics()
        shown = metrics.elidedText(
            self._full, Qt.TextElideMode.ElideRight, self.width()
        )
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class _SearchChip(QWidget):
    """One ``op:value`` token: the operator, its value and a × that removes it."""

    def __init__(
        self, op: str, value: str, on_remove: Callable[[_SearchChip], None]
    ) -> None:
        super().__init__()
        self.op = op
        self.value = value
        self.setObjectName("SearchChip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(CHIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        op_label = QLabel(f"{op}:")
        op_label.setObjectName("SearchChipOp")
        self.value_label = _ElidedLabel(value)
        self.value_label.setObjectName("SearchChipValue")
        self.remove_btn = QToolButton()
        self.remove_btn.setObjectName("SearchChipRemove")
        self.remove_btn.setFixedSize(16, 16)
        self.remove_btn.setIconSize(QSize(10, 10))
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Backspace removes chips from the keyboard; Tab skips the ×.
        self.remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_btn.clicked.connect(lambda: on_remove(self))
        self.refresh_theme()
        self.retranslate_ui()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 3, 0)
        layout.setSpacing(0)
        layout.addWidget(op_label)
        layout.addSpacing(4)
        layout.addWidget(self.value_label, 1)
        layout.addSpacing(4)
        layout.addWidget(self.remove_btn)

    def token(self) -> str:
        return f"{self.op}:{self.value}"

    def retranslate_ui(self) -> None:
        self.remove_btn.setToolTip(tr("search.remove_chip", token=self.token()))
        self.remove_btn.setAccessibleName(self.remove_btn.toolTip())

    def refresh_theme(self) -> None:
        self.remove_btn.setIcon(icons.icon("close", "text3", 10))


class _SearchInput(QLineEdit):
    """The free-text input: wide enough for its text, then whatever is left."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SearchInput")
        self.setFrame(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.textChanged.connect(self.updateGeometry)

    def sizeHint(self) -> QSize:
        text = self.text() or self.placeholderText()
        width = self.fontMetrics().horizontalAdvance(text) + 12
        return QSize(max(_INPUT_MIN_WIDTH, width), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return QSize(_INPUT_MIN_WIDTH, super().minimumSizeHint().height())


class SearchBar(QWidget):
    """Search field of the address bar; emits search_changed as the user types.

    It widens while a search is active and names the place it searches in its
    placeholder. Colours come from ``QFrame#SearchField`` in the global QSS.
    """

    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._location = ""
        self._tokens: list[tuple[str, str]] = []
        self._chips: list[_SearchChip] = []
        self._retired: list[QWidget] = []

        self._field = QFrame()
        self._field.setObjectName("SearchField")
        self._field.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._field.setProperty("focused", False)
        self._field.setFixedHeight(FIELD_HEIGHT)
        self._field.setCursor(Qt.CursorShape.IBeamCursor)
        self._field.installEventFilter(self)

        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        self._edit = _SearchInput()
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.textEdited.connect(self._on_text_edited)
        self._edit.installEventFilter(self)
        self._kbd = KbdHint("Ctrl F")
        self._clear_btn = QToolButton()
        self._clear_btn.setObjectName("SearchClear")
        self._clear_btn.setFixedSize(20, 20)
        self._clear_btn.setIconSize(QSize(12, 12))
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_btn.clicked.connect(self._on_clear_clicked)

        self._row = QHBoxLayout(self._field)
        self._row.setContentsMargins(10, 0, 6, 0)
        self._row.setSpacing(6)
        self._row.addWidget(self._icon)
        self._row.addWidget(self._edit, 1)
        self._row.addWidget(self._kbd)
        self._row.addWidget(self._clear_btn)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._field)

        self.setFixedWidth(WIDTH)
        self.refresh_theme()
        self.retranslate_ui()
        self._update_hints()

    # ------------------------------------------------------------------

    def retranslate_ui(self) -> None:
        self._edit.setToolTip(tr("search.help"))
        self._clear_btn.setToolTip(tr("search.clear"))
        self._clear_btn.setAccessibleName(self._clear_btn.toolTip())
        for chip in self._chips:
            chip.retranslate_ui()
        self._update_placeholder()

    def refresh_theme(self) -> None:
        self._icon.setPixmap(icons.pixmap("search", "text3"))
        self._clear_btn.setIcon(icons.icon("close", "text3", 12))
        for chip in self._chips:
            chip.refresh_theme()

    def set_location(self, location: str) -> None:
        """Name the place a search would cover, shown in the placeholder."""
        self._location = location
        self._update_placeholder()

    def set_active(self, active: bool) -> None:
        """Widen the field while a search is active."""
        self.setFixedWidth(ACTIVE_WIDTH if active else WIDTH)

    def text(self) -> str:
        """The search: the chips' tokens, then the free text as typed."""
        return join_tokens(self._tokens, self._edit.text())

    def tokens(self) -> list[tuple[str, str]]:
        return list(self._tokens)

    def chips(self) -> list[_SearchChip]:
        return list(self._chips)

    def free_text(self) -> str:
        return self._edit.text()

    def set_text_silently(self, text: str) -> None:
        """Show *text*, its tokens as chips, without emitting search_changed."""
        if text == self.text():
            return
        tokens, free = split_tokens(text)
        self._set_tokens(tokens)
        self._edit.blockSignals(True)
        self._edit.setText(free)
        self._edit.blockSignals(False)
        self._update_hints()

    def clear(self) -> None:
        """Remove every chip and the typed text."""
        if not self.text():
            return
        self._set_tokens([])
        self._edit.blockSignals(True)
        self._edit.clear()
        self._edit.blockSignals(False)
        self._changed()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()

    # ── Chips ──────────────────────────────────────────────────────────────

    def _set_tokens(self, tokens: list[tuple[str, str]]) -> None:
        # A chip is removed from its own × click, so it is deleted later:
        # detached first and held until then, like the tag field's chips.
        self._retired = [w for w in self._retired if shiboken6.isValid(w)]
        for chip in self._chips:
            self._row.removeWidget(chip)
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()
            self._retired.append(chip)
        self._tokens = list(tokens)
        self._chips = [
            _SearchChip(op, value, self._remove_chip) for op, value in self._tokens
        ]
        for index, chip in enumerate(self._chips):
            self._row.insertWidget(1 + index, chip)
        self._update_placeholder()

    def _remove_chip(self, chip: _SearchChip) -> None:
        if chip in self._chips:
            tokens = list(self._tokens)
            del tokens[self._chips.index(chip)]
            self._set_tokens(tokens)
            self._changed()
        self._edit.setFocus()

    def _absorb(self, *, final: bool) -> bool:
        """Turn the typed tokens into chips; True if any was.

        While typing only tokens a space has ended count, so the word under
        the cursor stays text; Enter and Tab (*final*) take every token.
        """
        text = self._edit.text()
        if final:
            head, tail = text, ""
        else:
            match = _LAST_SPACE_RE.search(text)
            if match is None:
                return False
            head, tail = text[: match.end()], text[match.end() :]
        tokens, free = split_tokens(head)
        if not tokens:
            return False
        if final:
            rest = free
        else:
            rest = f"{free} {tail}" if free else tail
        from_end = min(len(text) - self._edit.cursorPosition(), len(tail))
        self._set_tokens([*self._tokens, *tokens])
        self._edit.setText(rest)  # emits search_changed with the chips in
        self._edit.setCursorPosition(len(rest) - from_end)
        return True

    # ── Input ──────────────────────────────────────────────────────────────

    def _on_text_edited(self, _text: str) -> None:
        self._absorb(final=False)

    def _on_text_changed(self, _text: str) -> None:
        self._changed()

    def _on_clear_clicked(self) -> None:
        self.clear()
        self._edit.setFocus()

    def _changed(self) -> None:
        self._update_hints()
        self.search_changed.emit(self.text())

    def _update_placeholder(self) -> None:
        if self._tokens:
            text = ""
        elif self._location:
            text = tr("search.placeholder_in", location=self._location)
        else:
            text = tr("search.placeholder_short")
        self._edit.setPlaceholderText(text)

    def _update_hints(self) -> None:
        """The shortcut shows while the field is empty, the clear button after."""
        empty = not self._tokens and not self._edit.text()
        self._kbd.setVisible(empty)
        self._clear_btn.setVisible(not empty)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if watched is self._field and kind == QEvent.Type.MouseButtonPress:
            self._edit.setFocus(Qt.FocusReason.MouseFocusReason)
        elif watched is self._edit:
            if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self._field.setProperty("focused", kind == QEvent.Type.FocusIn)
                _repolish(self._field)
            elif kind == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                return self._on_key(event)
        return super().eventFilter(watched, event)

    def _on_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        plain = not event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        )
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._absorb(final=True)
            return True
        if key == Qt.Key.Key_Tab and plain:
            # Tab ends a token; with none to end, it moves focus as usual.
            return self._absorb(final=True)
        if (
            key == Qt.Key.Key_Backspace
            and plain
            and not self._edit.text()
            and self._chips
        ):
            self._remove_chip(self._chips[-1])
            return True
        return False
