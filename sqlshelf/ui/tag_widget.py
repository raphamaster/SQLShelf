from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme.tokens import CHIP_DELETE_BG, CHIP_DELETE_FG, TAG_BG, TAG_RADIUS, TAG_TEXT

# ---------------------------------------------------------------------------
# Flow layout (wrapping horizontal layout)
# ---------------------------------------------------------------------------

class _FlowLayout(QLayout):
    """Items flow left-to-right and wrap to the next row when width is exceeded."""

    def __init__(self, parent: QWidget | None = None, h_gap: int = 4, v_gap: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_gap = h_gap
        self._v_gap = v_gap

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool = False) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_h = 0
        right = rect.right() - m.right()

        for item in self._items:
            hint = item.sizeHint()
            w, h = hint.width(), hint.height()
            if x + w > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + self._v_gap
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += w + self._h_gap
            line_h = max(line_h, h)

        return y + line_h - rect.y() + m.bottom()


# ---------------------------------------------------------------------------
# Shared chip styles
# ---------------------------------------------------------------------------

def _display_chip_style(bg: str, fg: str) -> str:
    return (
        f"background-color: {bg}; color: {fg}; "
        f"border-radius: {TAG_RADIUS}px; padding: 2px 9px; font-size: 11px;"
    )


def _input_chip_style(bg: str, fg: str) -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
        f"border-radius: {TAG_RADIUS - 1}px; padding: 1px 6px; font-size: 11px; text-align: left; max-height: 20px; }} "
        f"QPushButton:hover {{ background-color: {CHIP_DELETE_BG}; color: {CHIP_DELETE_FG}; }}"
    )

_CHIP_BG = TAG_BG
_CHIP_FG = TAG_TEXT


# ---------------------------------------------------------------------------
# TagDisplayWidget — read-only chips
# ---------------------------------------------------------------------------

class TagDisplayWidget(QWidget):
    """Displays tags as colored chips with rounded corners (read-only)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow = _FlowLayout(self, h_gap=4, v_gap=2)
        self.setLayout(self._flow)

    def set_tags(self, tags: list[str]) -> None:
        self._clear()
        for tag in tags:
            chip = QLabel(f"#{tag}")
            chip.setStyleSheet(_display_chip_style(_CHIP_BG, _CHIP_FG))
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._flow.addWidget(chip)
        self.updateGeometry()

    def _clear(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()


# ---------------------------------------------------------------------------
# TagInputWidget — editable chips
# ---------------------------------------------------------------------------

class TagInputWidget(QWidget):
    """Chip-style tag editor: existing tags show as removable badges, with an
    inline QLineEdit to add new ones (press Enter or comma to confirm)."""

    tags_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []

        # Container for chips + input, all in a flow layout
        self._flow_widget = QWidget()
        self._flow = _FlowLayout(self._flow_widget, h_gap=4, v_gap=3)
        self._flow_widget.setLayout(self._flow)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Add tag, press Enter…")
        self._input.setFixedHeight(20)
        self._input.setStyleSheet(
            "border: none; background: transparent; min-width: 100px; font-size: 11px;"
        )
        self._input.returnPressed.connect(self._add_from_input)
        # Also trigger on comma
        self._input.textChanged.connect(self._check_comma)
        self._flow.addWidget(self._input)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._flow_widget)
        self.setMaximumHeight(44)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_tags(self, tags: list[str]) -> None:
        self._tags = list(tags)
        self._rebuild()

    def get_tags(self) -> list[str]:
        return list(self._tags)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        # Remove all chips (but keep self._input)
        to_remove = []
        for i in range(self._flow.count()):
            item = self._flow.itemAt(i)
            if item and item.widget() and item.widget() is not self._input:
                to_remove.append(item.widget())
        for w in to_remove:
            self._flow.removeWidget(w)
            w.hide()
            w.deleteLater()

        # Re-insert chips before the input
        for tag in self._tags:
            chip = QPushButton(f"#{tag}  ×")
            chip.setStyleSheet(_input_chip_style(_CHIP_BG, _CHIP_FG))
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            chip.clicked.connect(lambda checked=False, t=tag: self._remove_tag(t))
            # Insert before the input field
            idx = self._flow.indexOf(self._input) if hasattr(self._flow, "indexOf") else -1
            self._flow.addWidget(chip)  # added at end; we'll reorder via takeAt below
        # Reorder: move _input to the end
        self._ensure_input_last()
        self.updateGeometry()

    def _ensure_input_last(self) -> None:
        """Move the _input item to the last position in the flow layout."""
        for i in range(self._flow.count()):
            item = self._flow.itemAt(i)
            if item and item.widget() is self._input:
                if i != self._flow.count() - 1:
                    self._flow.takeAt(i)
                    self._flow.addItem(item)
                break

    def _add_from_input(self) -> None:
        raw = self._input.text().replace(",", " ")
        for part in raw.split():
            tag = part.strip("#").strip().lower()
            if tag and tag not in self._tags:
                self._tags.append(tag)
        self._input.clear()
        self._rebuild()
        self.tags_changed.emit(self._tags)

    def _check_comma(self, text: str) -> None:
        if "," in text:
            self._add_from_input()

    def _remove_tag(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
            self._rebuild()
            self.tags_changed.emit(self._tags)
