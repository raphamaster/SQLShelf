"""The edit form's tag field: chips with an inline input and a suggestion popup.

Typing filters the library's tags, with how many queries use each, in a popup
under the field. Up/Down pick a suggestion; Enter adds it, or creates the
typed tag when none is picked; a comma or space ends a tag; Backspace in the
empty input removes the last chip; Esc closes the popup before it can cancel
the edit. The popup is a child of the window rather than a ``Qt.Popup``, so
the input keeps the keyboard focus while it is open.
"""

from __future__ import annotations

from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QFocusEvent,
    QHideEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from ..core.tag_entry import normalize_tag, split_tags, suggest_tags
from ..core.text_match import find_span
from . import icons
from . import tokens as _tk
from .tag_widget import FlowLayout
from .theme import qcolor
from .widgets import KbdHint

PILL_HEIGHT = 22
POPUP_WIDTH = 280
ROW_HEIGHT = 30
MAX_SUGGESTIONS = 6
_GAP = 6
_INPUT_MIN_WIDTH = 80


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class _EditPill(QWidget):
    """A chosen tag: its colour dot, the name and a × that removes it."""

    def __init__(self, tag: str, on_remove: Callable[[str], None]) -> None:
        super().__init__()
        self.tag = tag
        self.setObjectName("EditTagPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(PILL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setPixmap(icons.dot(_tk.tag_color(tag)))
        name = QLabel(tag)
        name.setObjectName("EditTagPillText")
        self.remove_btn = QToolButton()
        self.remove_btn.setObjectName("EditTagPillRemove")
        self.remove_btn.setFixedSize(16, 16)
        self.remove_btn.setIconSize(QSize(10, 10))
        self.remove_btn.setIcon(icons.icon("close", "text3", 10))
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Backspace removes chips from the keyboard; Tab skips the ×.
        self.remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_btn.clicked.connect(lambda: on_remove(tag))
        self.retranslate_ui()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 3, 0)
        layout.setSpacing(6)
        layout.addWidget(dot)
        layout.addWidget(name)
        layout.addWidget(self.remove_btn)

    def retranslate_ui(self) -> None:
        self.remove_btn.setToolTip(tr("tags.remove", tag=self.tag))
        self.remove_btn.setAccessibleName(self.remove_btn.toolTip())


class _TagInput(QLineEdit):
    """The inline input: as wide as its text, never narrower than a few chars."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TagFieldInput")
        self.setFrame(False)
        self.setFixedHeight(PILL_HEIGHT)
        self.textChanged.connect(self.updateGeometry)

    def sizeHint(self) -> QSize:
        text = self.text() or self.placeholderText()
        width = self.fontMetrics().horizontalAdvance(text) + 16
        return QSize(max(_INPUT_MIN_WIDTH, width), PILL_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(_INPUT_MIN_WIDTH, PILL_HEIGHT)


class _SuggestionList(QWidget):
    """Rows of dot, name (the typed part marked) and count, painted directly.

    Rows take no focus, so clicking one leaves the keyboard in the input.
    """

    picked = Signal(str)
    current_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TagSuggestions")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._items: list[tuple[str, int]] = []
        self._typed = ""
        self._current = -1
        self._hover = -1

    def set_items(self, items: list[tuple[str, int]], typed: str) -> None:
        self._items = list(items)
        self._typed = typed
        self._current = -1
        self._hover = -1
        self.setFixedHeight(len(self._items) * ROW_HEIGHT)
        self.update()

    def items(self) -> list[tuple[str, int]]:
        return list(self._items)

    def current(self) -> int:
        return self._current

    def current_tag(self) -> str | None:
        if 0 <= self._current < len(self._items):
            return self._items[self._current][0]
        return None

    def move_current(self, step: int) -> None:
        count = len(self._items)
        if not count:
            return
        if self._current < 0:
            self._current = 0 if step > 0 else count - 1
        else:
            self._current = (self._current + step) % count
        self.update()
        self.current_changed.emit()

    def _row_at(self, y: int) -> int:
        row = y // ROW_HEIGHT
        return row if 0 <= row < len(self._items) else -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        row = self._row_at(event.position().toPoint().y())
        if row != self._hover:
            self._hover = row
            self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover = -1
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        row = self._row_at(event.position().toPoint().y())
        if row >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self._items[row][0])

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        metrics = self.fontMetrics()
        radius = _tk.RADIUS_SM
        for row, (name, count) in enumerate(self._items):
            rect = QRect(0, row * ROW_HEIGHT, self.width(), ROW_HEIGHT)
            if row in (self._current, self._hover):
                key = "sel_soft" if row == self._current else "hover"
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(_tk.color(key)))
                painter.drawRoundedRect(rect.adjusted(0, 1, 0, -1), radius, radius)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(_tk.tag_color(name)))
            painter.drawEllipse(QPoint(rect.left() + 14, rect.center().y() + 1), 4, 4)

            count_text = str(count)
            count_width = metrics.horizontalAdvance(count_text)
            right = rect.right() - 10
            painter.setPen(qcolor(_tk.color("text3")))
            painter.drawText(
                QRect(right - count_width, rect.top(), count_width, ROW_HEIGHT),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                count_text,
            )

            left = rect.left() + 28
            width = right - count_width - 12 - left
            shown = metrics.elidedText(name, Qt.TextElideMode.ElideRight, width)
            span = find_span(name, self._typed)
            if span is not None and shown == name:
                start, end = span
                x = left + metrics.horizontalAdvance(name[:start])
                mark = QRect(
                    x,
                    rect.center().y() - metrics.height() // 2,
                    metrics.horizontalAdvance(name[start:end]),
                    metrics.height(),
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(_tk.color("mark")))
                painter.drawRoundedRect(mark, 2, 2)
            painter.setPen(qcolor(_tk.color("text")))
            painter.drawText(
                QRect(left, rect.top(), width, ROW_HEIGHT),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                shown,
            )
        painter.end()


class _TagPopup(QFrame):
    """ "Existing tags", the suggestions, then what Enter will do."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TagPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(POPUP_WIDTH)

        self.header = QLabel()
        self.header.setObjectName("TagPopupHeader")
        self.list = _SuggestionList()
        self.divider = QFrame()
        self.divider.setObjectName("TagPopupDivider")
        self.divider.setFixedHeight(1)
        self.footer = QWidget()
        self.footer_text = QLabel()
        self.footer_text.setObjectName("TagPopupFooter")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(10, 6, 10, 2)
        footer_layout.setSpacing(6)
        footer_layout.addWidget(KbdHint("Enter"))
        footer_layout.addWidget(self.footer_text, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(2)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(10, 2, 10, 2)
        header_row.addWidget(self.header)
        layout.addLayout(header_row)
        layout.addWidget(self.list)
        layout.addWidget(self.divider)
        layout.addWidget(self.footer)
        self.hide()


class TagField(QFrame):
    """The chosen tags as chips, an inline input and the suggestion popup.

    Signals:
        tags_changed(list) — a tag was added or removed.
    """

    tags_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TagField")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("focused", False)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self._tags: list[str] = []
        self._counts: list[tuple[str, int]] = []
        self._pills: list[_EditPill] = []
        self._retired: list[QWidget] = []
        self._popup: _TagPopup | None = None

        self._flow = FlowLayout(self, h_gap=_GAP, v_gap=_GAP)
        self._flow.setContentsMargins(6, 6, 6, 6)
        self._input = _TagInput()
        self._input.installEventFilter(self)
        self._input.textEdited.connect(self._on_text_edited)
        self._flow.addWidget(self._input)

        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.retranslate_ui()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_tags(self, tags: list[str]) -> None:
        """Show *tags* as chips and empty the input."""
        self._tags = []
        for tag in tags:
            tag = normalize_tag(str(tag))
            if tag and tag not in self._tags:
                self._tags.append(tag)
        self._input.clear()
        self._rebuild()
        self._hide_popup()

    def tags(self) -> list[str]:
        """The chosen tags, plus any still typed in the input."""
        pending = [
            tag for tag in split_tags(self._input.text()) if tag not in self._tags
        ]
        return [*self._tags, *pending]

    def set_known_tags(self, counts: list[tuple[str, int]]) -> None:
        """The library's ``(tag, queries using it)`` pairs to suggest from."""
        self._counts = list(counts)
        if self._popup_visible():
            self._refresh_popup()

    def input(self) -> QLineEdit:
        return self._input

    def popup_visible(self) -> bool:
        return self._popup_visible()

    def suggestions(self) -> list[tuple[str, int]]:
        return self._popup.list.items() if self._popup is not None else []

    def footer_text(self) -> str:
        if self._popup is None or self._popup.footer.isHidden():
            return ""
        return self._popup.footer_text.text()

    def retranslate_ui(self) -> None:
        self._update_placeholder()
        for pill in self._pills:
            pill.retranslate_ui()
        if self._popup is not None:
            self._popup.header.setText(tr("tags.existing"))
            self._update_footer()

    def refresh_theme(self) -> None:
        self._rebuild()
        if self._popup is not None:
            self._popup.list.update()

    # ── Chips ──────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        # A chip is removed from its own × click, so it is deleted later:
        # detached first and held until then, like the details panel's chips.
        self._retired = [w for w in self._retired if shiboken6.isValid(w)]
        while self._flow.count():
            self._flow.takeAt(0)
        for pill in self._pills:
            pill.hide()
            pill.setParent(None)
            pill.deleteLater()
            self._retired.append(pill)
        self._pills = [_EditPill(tag, self._remove_tag) for tag in self._tags]
        for pill in self._pills:
            self._flow.addWidget(pill)
        self._flow.addWidget(self._input)
        self._update_placeholder()
        self._flow.invalidate()
        self.updateGeometry()

    def _update_placeholder(self) -> None:
        self._input.setPlaceholderText("" if self._tags else tr("tags.placeholder"))

    def _add_tags(self, tags: list[str]) -> None:
        added = [tag for tag in tags if tag and tag not in self._tags]
        if not added:
            return
        self._tags.extend(added)
        self._rebuild()
        self.tags_changed.emit(list(self._tags))

    def _remove_tag(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
            self._rebuild()
            self.tags_changed.emit(list(self._tags))
        self._input.setFocus()

    # ── Input ──────────────────────────────────────────────────────────────

    def _on_text_edited(self, text: str) -> None:
        cut = max(text.rfind(","), *(text.rfind(space) for space in " \t"))
        if cut >= 0:
            self._input.setText(text[cut + 1 :])
            self._add_tags(split_tags(text[:cut]))
        self._refresh_popup()

    def _commit(self) -> None:
        """Enter: add the picked suggestion, or the typed tags."""
        popup = self._popup
        picked = popup.list.current_tag() if popup and not popup.isHidden() else None
        tags = [picked] if picked is not None else split_tags(self._input.text())
        self._input.clear()
        self._add_tags(tags)
        self._hide_popup()

    def _pick(self, tag: str) -> None:
        self._input.clear()
        self._add_tags([tag])
        self._hide_popup()
        self._input.setFocus()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self._input:
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            focused = kind == QEvent.Type.FocusIn
            self.setProperty("focused", focused)
            _repolish(self)
            if not focused and isinstance(event, QFocusEvent):
                self._hide_popup()
        elif kind == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            # Esc first closes the popup; only then may it cancel the edit.
            if event.key() == Qt.Key.Key_Escape and self._popup_visible():
                event.accept()
                return True
        elif kind == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            return self._on_key(event)
        return False

    def _on_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key == Qt.Key.Key_Escape and self._popup_visible():
            self._hide_popup()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            return True
        if key == Qt.Key.Key_Down:
            if self._popup_visible():
                self._popup_list_step(1)
            else:
                self._refresh_popup(show_all=True)
            return True
        if key == Qt.Key.Key_Up and self._popup_visible():
            self._popup_list_step(-1)
            return True
        if (
            key == Qt.Key.Key_Backspace
            and not self._input.text()
            and self._tags
            and not event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._remove_tag(self._tags[-1])
            return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        self._input.setFocus()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._popup_visible():
            self._place_popup()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._hide_popup()

    # ── Popup ──────────────────────────────────────────────────────────────

    def _popup_visible(self) -> bool:
        return self._popup is not None and not self._popup.isHidden()

    def _ensure_popup(self) -> _TagPopup:
        window = self.window()
        if self._popup is not None and self._popup.parent() is window:
            return self._popup
        if self._popup is not None:
            self._popup.deleteLater()
        popup = _TagPopup(window)
        popup.header.setText(tr("tags.existing"))
        popup.list.picked.connect(self._pick)
        popup.list.current_changed.connect(self._update_footer)
        # The popup lives on the window; it goes with the field.
        self.destroyed.connect(popup.deleteLater)
        self._popup = popup
        return popup

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    def _popup_list_step(self, step: int) -> None:
        if self._popup is not None:
            self._popup.list.move_current(step)

    def _refresh_popup(self, show_all: bool = False) -> None:
        typed = normalize_tag(self._input.text())
        if not typed and not show_all:
            self._hide_popup()
            return
        items = suggest_tags(self._counts, typed, self._tags, MAX_SUGGESTIONS)
        if not items and not typed:
            self._hide_popup()
            return
        popup = self._ensure_popup()
        popup.list.set_items(items, typed)
        popup.header.setVisible(bool(items))
        popup.list.setVisible(bool(items))
        self._update_footer()
        self._place_popup()
        popup.show()
        popup.raise_()

    def _update_footer(self) -> None:
        popup = self._popup
        if popup is None:
            return
        picked = popup.list.current_tag()
        typed = normalize_tag(self._input.text())
        if picked is not None:
            text = tr("tags.enter_adds", tag=picked)
        elif typed and typed not in self._tags:
            known = any(name == typed for name, _count in self._counts)
            key = "tags.enter_adds" if known else "tags.enter_creates"
            text = tr(key, tag=typed)
        else:
            text = ""
        popup.footer_text.setText(text)
        visible = bool(text)
        popup.footer.setVisible(visible)
        popup.divider.setVisible(visible and popup.list.isVisibleTo(popup))
        popup.adjustSize()

    def _place_popup(self) -> None:
        popup = self._popup
        if popup is None:
            return
        window = popup.parentWidget()
        if window is None:
            return
        popup.adjustSize()
        height = popup.sizeHint().height()
        popup.resize(POPUP_WIDTH, height)
        below = self.mapTo(window, QPoint(0, self.height() + 4))
        y = below.y()
        if y + height > window.height() - 4:
            y = max(4, self.mapTo(window, QPoint(0, 0)).y() - height - 4)
        x = max(4, min(below.x(), window.width() - POPUP_WIDTH - 4))
        popup.move(x, y)
